# Certificate renewal and in-place reload

Status: design accepted 2026-11-15 (renewal runs as a host timer; private CA keys stay
in their owner-only host directories). Follow-up item 2 of
[1015 follow-up](../1015-embedded-hud-sign-in/follow-up.md).

## Problem

Every reviewer/dispatch TLS endpoint reads its certificate material once, at process
start. cert-manager renews the browser certificate, but the reviewer HUD keeps serving
the old one until restarted, and restarting it signs every reviewer out (sessions are
in memory). The private-CA leaves (internal API server, HUD installation client,
receiver HTTPS) are issued and rotated by hand, and nothing alerts before they expire.

| Material | Issuer | Loaded by | Expires |
|---|---|---|---|
| Browser server cert | cert-manager (Let's Encrypt DNS01) | reviewer HUD server | renews ~30 d before 90 d |
| Internal API server cert | private API CA | reviewer API server | 2027-11-14 |
| Installation client cert | private installation CA | reviewer HUD client; pinned by API enrollment | 2027-11-14 |
| Receiver HTTPS cert | private receiver CA | alert receiver `:8446`; trusted by relay | 2027-11-14 |
| The three private CAs | self-signed | trust bundles in API, HUD, relay | 2031/2036 |

## Design

Three independent parts. None of them changes authentication, authorization, session
handling or the request paths.

### 1. In-place reload (repository code)

Each process watches the files it already reads, polling `stat` (kubelet replaces a
Secret volume by swapping the `..data` symlink, so the mount must not use `subPath`;
inotify on the symlink target is unreliable). When any watched file changes, the
process reads the whole set and builds a candidate. The candidate is applied only if:

- the certificate chain parses and the private key matches the leaf certificate;
- the leaf is currently valid (`notBefore <= now < notAfter`). The Node processes check
  this themselves. The receiver stays standard-library only and checks what its startup
  already checks (parses, key matches). The renewal tool verifies validity before it
  publishes, and the served-certificate probe would catch anything else;
- for an enrollment file, it passes the existing `parseEnrollments` validation.

On failure the process keeps serving the previous material, logs one line per distinct
failure (never key or certificate contents), and retries on the next change. A
half-written update (cert from the new pair, key from the old) is therefore rejected
until both files are consistent.

Swap points, all without restarting or dropping established connections:

- **Reviewer HUD server**: `server.setSecureContext({cert, key})`. In-memory sessions
  survive, so browser renewal no longer signs reviewers out.
- **Reviewer HUD client** (installation identity + API CA): build a new `https.Agent`
  and switch to it; the old agent is destroyed after the request deadline.
- **Reviewer API**: `server.setSecureContext({ca, cert, key})`; the enrollment map is
  replaced atomically. Several fingerprints may map to one installation, which is the
  overlap that lets an installation certificate rotate without a 403 window.
- **Relay**: receiver CA bundle reload, same agent swap as the HUD client.
- **Alert receiver**: build and validate a fresh `ssl.SSLContext`, then replace the
  context each `TLSListener` uses for new handshakes.

The HUD (ESM, own image) and API/relay (CommonJS, other image) each carry a small
reloader with the same contract and tests. Sharing one file would require changing both
image build contexts; that coupling is not worth ~60 lines.

### 2. Host renewal timer

`setup/reviewer_tls.py` (stdlib + the `openssl` CLI the CAs were created with) with
`plan`, `renew` and `status` commands, run daily by a systemd user timer. For each leaf
it renews when fewer than `--renew-before` days (default 30) remain:

1. Generate a new key and CSR reusing the current subject and SANs; sign with that
   leaf's CA (365 days); verify chain, key match and SANs before writing anything.
2. Publish atomically: Kubernetes Secrets by server-side apply of only the affected
   keys; host files by write-to-temp then `rename`. Previous material is retained
   beside the CA records for rollback.
3. Installation certificate rotation is ordered: add the new fingerprint to the
   enrollment Secret, wait until the API has the new enrollment, then switch the
   installation Secret, wait until the HUD uses it, and remove the old fingerprint.

CA rollover is not automated (CAs last until 2031/2036). The reloaders accept
multi-certificate CA bundles, so an operator rollover can publish old+new CA, rotate
leaves, then drop the old CA, with no restarts.

### 3. Expiry and renewal monitoring

- Blackbox TLS probes of the served endpoints (browser `:443`, receiver `:8446`) report
  `probe_ssl_earliest_cert_expiry`. Because it measures what is served, it also proves a
  reload happened.
- The renewal tool pushes `convergence_tls_cert_not_after_seconds{name}` for every leaf
  (including the client-only installation certificate) and
  `convergence_tls_renewal_last_success_seconds` to the existing Pushgateway.
- vmalert rules: certificate expires within 14 days (renewal should have run at 30),
  and no successful renewal run for 3 days.

## Qualification before any live change

Isolated tests with short-lived certificates from throwaway CAs: renewal applied
without restart; a mismatched key/cert pair or expired leaf is rejected and the old
material keeps serving; recovery after a later good write; CA bundle rollover; enrollment
overlap with no 403 window; sessions retained across HUD reload. Then a live dry run
(`plan`) before enabling the timer.

## Out of scope

Changing browser TLS issuance, CA rollover automation, JEV and native HUD work.
