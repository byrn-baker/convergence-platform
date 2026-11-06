# Proxmox snapshot shipper

Run this on the management backend, outside the customer guests. It reads the passive observer's bounded snapshot through an existing pinned SSH connection and publishes it to the separate hypervisor evidence endpoint using validated HTTPS and a dedicated publisher credential. No new hypervisor listener or token is needed.

Configure a root-owned JSON file with `url` (HTTPS `/v1/hypervisor-events`), `node`, `ssh_host` (`root@management-address`), absolute `identity_file`, `known_hosts_file`, `ca_file`, `status_file` and optional `interval_seconds` (5–60, default15). The service uses systemd `LoadCredential=ingest-token`; an explicit `token_file` is available for controlled one-shot validation. Keep the token out of command arguments, source control and logs. The receiver stores only its SHA-256 hash. Configure the trusted site and node in the receiver; they are not caller-controlled tenant claims.

The supplied unit runs as the existing backend SSH operator with a read-only home, strict host-key checking, no privilege escalation, CPU5% and memory96MiB. Keep only the verified Proxmox host key(s), plus the verified publisher host key when relaying, in the configured known-hosts file. Install the observer first, initially with a restricted test-VM capture list. Verify real snapshots against the receiver before publication, then activate the shipper. Verify receiver freshness and the Convergence Security card before broadening capture.

Snapshots represent bounded rolling observations, not a complete replay queue. A disconnected or overloaded pipeline can lose connection metadata; interface counters remain cumulative and status becomes stale after60seconds. The receiver retains bounded snapshot-observation history, not unique-connection counts. No case creation or quarantine is performed.

Rollback: stop/disable this unit and the Proxmox observer, retain evidence for inspection, restore the previous managed API/ingest image digests and HUD backup, and remove only this source's receiver configuration using a resource-version-checked update. Do not remove or reset unrelated source credentials or VM firewall state.


## Optional management SSH relay

When the backend can reach Proxmox over SSH but cannot reach the receiver over HTTPS, set `relay_host` to `ubuntu@192.168.3.252` (or another verified publisher IP/DNS hostname). The backend still reads the same Proxmox snapshot. It sends the bounded JSON through pinned SSH to a one-shot publisher on the relay host. No new listening service, firewall rule, SSH agent forwarding, credential forwarding, or TLS exception is required.

The backend relay configuration needs `node`, `ssh_host`, `relay_host`, `identity_file`, and `known_hosts_file`, with optional `status_file` and `interval_seconds`. Its known-hosts file must contain independently verified keys for both SSH destinations. Relay mode never reads `token_file`, systemd `CREDENTIALS_DIRECTORY`, or `ca_file`, and does not need a receiver URL. Clear the backend unit's publisher credential dependency when deploying this mode so systemd itself does not load an unused credential. Create `/etc/systemd/system/netclaw-proxmox-shipper.service.d/relay.conf` on the backend with:

```ini
[Service]
LoadCredential=
```

After installing the relay config, script, and verified host pins, run `systemctl daemon-reload` and restart `netclaw-proxmox-shipper.service`. This drop-in applies only to the backend; publisher files remain private local inputs. Remove the drop-in and restore the backend publisher credential before returning to direct HTTPS mode.

Install the same `shipper.py` on the publisher at `/home/ubuntu/.local/lib/netclaw-proxmox-shipper/shipper.py`. The SSH invocation is fixed:

```sh
/usr/bin/python3 -I /home/ubuntu/.local/lib/netclaw-proxmox-shipper/shipper.py --config /home/ubuntu/.config/netclaw-proxmox-publisher/config.json --publish-stdin
```

The publisher config contains only the local HTTPS settings:

```json
{
  "node": "YOUR_CONFIGURED_PROXMOX_NODE",
  "url": "https://YOUR_RECEIVER/v1/hypervisor-events",
  "ca_file": "/home/ubuntu/.config/netclaw-proxmox-publisher/receiver-ca.pem",
  "token_file": "/home/ubuntu/.config/netclaw-proxmox-publisher/ingest-token"
}
```

Protect that directory with mode `0700` and the config/token with mode `0600`; the credential stays on the publisher. The publisher needs no Proxmox SSH identity or known-hosts file. `--publish-stdin` reads at most 512 KiB plus one overflow-detection byte, rejects stale/future snapshots, wrong node/schema, duplicate keys, and nonfinite JSON numbers, then publishes with the local token and trusted CA. HTTPS redirects remain disabled. The receiver performs the complete evidence-schema and identity validation.

A successful one-shot publication emits only `{"status":"accepted"}`. Errors emit an exception class without error text or credentials and return a nonzero exit code. The backend accepts only that exact bounded acknowledgment, discards SSH stderr, and limits the relay exchange to 20 seconds. Normal direct HTTPS publication remains the default when `relay_host` is absent.

Local validation: `python3 -m unittest discover -s services/proxmox-shipper -p test_shipper.py -v`.
