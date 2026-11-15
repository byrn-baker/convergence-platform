# Reviewer and receiver TLS renewal

The HTTPS reviewer and investigation dispatch use three private CAs, separate from the
browser certificate that cert-manager renews:

| Leaf | Issued by | Published to | Read by |
|---|---|---|---|
| Reviewer API server | API CA | Secret with `tls.crt`/`tls.key` (`reviewer.serviceTlsSecret`) | reviewer API |
| Reviewer installation client | installation CA | Secret `reviewer.installationTlsSecret`; its pin is in the API's `enrolled.json` | reviewer HUD (client), reviewer API (pin) |
| Receiver HTTPS | receiver CA | host files named by `REVIEWER_HTTPS_CERT_FILE`/`_KEY_FILE` | alert receiver `:8446`; the relay trusts the CA |

`setup/reviewer_tls.py` renews these leaves on the host that holds the CA keys, from a
systemd user timer. The running services pick up renewed files in place without a
restart (`specs/1016-tls-renewal-reload`), so reviewers stay signed in.

## What a run does

Every six hours the timer runs `renew`. A leaf is due when fewer than
`renew_before_days` (default 30) remain. For a due leaf the tool:

1. checks that the current certificate was issued by the configured CA;
2. issues a new P-256 key and a certificate valid for `validity_days` (default 365),
   copying the subject, SANs and key usages of the current certificate;
3. verifies the chain, key match, identical profile and validity before writing;
4. archives the current pair under `<state_dir>/archive/<leaf>/<time>/` (owner-only);
5. publishes with a compare-and-swap: a Secret patch whose `test` operation fails if the
   certificate changed since it was read, or an atomic rename of host files (key first,
   so a reader never sees a matching half-update);
6. updates the private record copy, when configured.

The installation leaf is pinned, so it rotates in three steps, one per run and at least
`settle_seconds` (default one hour) apart, giving each service time to reload:

1. **enroll**: add the new pin to `enrolled.json` beside the old one;
2. **publish**: put the new identity in the installation Secret;
3. **unpin**: remove the old pin.

Each step is recorded before it runs and is idempotent, so a run that stops midway
resumes the same step. A rotation therefore takes at least three runs.

## Configure

The config file names paths only, never key contents. Keep it owner-only.

```json
{
  "kubeconfig": "~/path/to/kubeconfig",
  "state_dir": "~/.local/state/convergence/reviewer-tls",
  "renew_before_days": 30,
  "validity_days": 365,
  "settle_seconds": 3600,
  "pushgateway": {"namespace": "observability", "service": "pushgateway:9091"},
  "leaves": [
    {"name": "receiver",
     "ca": {"cert": "~/.config/convergence/reviewer/dispatch/ca.crt",
            "key": "~/.config/convergence/reviewer/dispatch/ca.key"},
     "files": {"cert": "~/.config/convergence/reviewer/dispatch/receiver.crt",
               "key": "~/.config/convergence/reviewer/dispatch/receiver.key"}},
    {"name": "reviewer-api",
     "ca": {"cert": "<pki>/api-ca.crt", "key": "<pki>/api-ca.key"},
     "secret": {"namespace": "netclaw-convergence", "name": "convergence-reviewer-service-tls"},
     "record": {"cert": "<pki>/service.crt", "key": "<pki>/service.key"}},
    {"name": "installation",
     "ca": {"cert": "<pki>/installation-ca.crt", "key": "<pki>/installation-ca.key"},
     "secret": {"namespace": "netclaw-convergence", "name": "convergence-reviewer-installation-tls"},
     "record": {"cert": "<pki>/installation.crt", "key": "<pki>/installation.key"},
     "enrollment": {"namespace": "netclaw-convergence", "name": "convergence-reviewer-service-tls"}}
  ]
}
```

The Secrets must be mounted without `subPath` (the chart does this), so kubelet updates
them in place. `pushgateway` is optional. The push goes through the Kubernetes API service
proxy using the same kubeconfig, so no new port is exposed.

## Run

**Install the timer only after a release with in-place reload runs everywhere** (reviewer
API, HUD and relay images, and the receiver). An older HUD would not pick up a renewed
installation identity, and the unpin step would then lock it out with 403s. `plan` is
safe at any time.

```sh
python3 setup/reviewer_tls.py plan --config ~/.config/convergence/reviewer/tls-renewal.json
python3 setup/reviewer_tls.py install --config ~/.config/convergence/reviewer/tls-renewal.json --start
python3 setup/reviewer_tls.py status --config ~/.config/convergence/reviewer/tls-renewal.json
```

`plan` and `status` change nothing; they show each leaf's expiry, days left, the next
action and any rotation in progress, and flag a record copy that differs from what is
published. Run `install` from the installed checkout: the timer runs `renew` from the
checkout that installed it. A user timer runs at boot only with lingering enabled
(`loginctl enable-linger`). `remove` disables the timer and deletes only its units,
keeping state, archives and any pending certificate.

## Monitoring

With `pushgateway` set, each run pushes:

- `convergence_tls_cert_not_after_seconds{leaf}`: expiry of each published leaf;
- `convergence_tls_renewal_last_run_seconds`, `convergence_tls_renewal_failed_leaves`;
- `convergence_tls_renewal_last_success_seconds`, only when every leaf succeeded.

Scrape the Pushgateway for `convergence_tls_.*` and alert when a leaf has under 14 days
left (renewal should have happened at 30) or no run has fully succeeded for three days.
Probe served endpoints too (`probe_ssl_earliest_cert_expiry`): that measures what clients
actually see, so it also proves a reload happened.

## Roll back

Each publish leaves the previous pair in `<state_dir>/archive/<leaf>/<time>/`. To restore
it, put those files back in the Secret or host files; the services reload them in place.
For the installation leaf, also make sure `enrolled.json` contains the restored
certificate's pin. Rolling back past an `unpin` step means re-enrolling the old pin first.

CA rollover is not automated (the CAs outlive many leaf renewals). The reloaders accept
multi-certificate CA bundles, so publish old+new CA, renew the leaves under the new CA,
then drop the old one.
