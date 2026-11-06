# Acceptance checks

Record each selected capability as verified, failing, or awaiting a named
prerequisite. A fresh core without device telemetry is a working foundation,
not a fully monitored home network.

| Layer | Evidence required |
|---|---|
| Core | API health reports diary/database available; correct site token reads cases; Prometheus and Alertmanager APIs work; expected scrape targets are healthy |
| Routing/auth | Container/pod reaches receiver; unauthenticated writes fail; tokens/sites match across HUD, receiver and investigator |
| Telemetry | Fresh sample from each selected device/source, with correct site/device labels and timestamps; disabled/unknown adapters are explicit |
| HUD | User's intended LAN/subnet or authenticated proxy reaches frontend and API; real site selected; Grafana uses its own port |
| Model | Actual configured model completes a small request through the selected runtime/authentication path; no placeholder model IDs |
| Cases | A marked synthetic canary reaches the selected database once; resolution/clearance tracked separately; collection alone does not trigger an LLM |
| Guardian | Selected member can read/update its test case with scoped tools; authorized bounded investigation records evidence and uncertainty; learning completion separately verified |
| IDS | Controlled signature/canary and observation counters prove the actual capture path; requested VLANs marked observed/unverified; drops and lag measured |
| Victoria | Recent IDS/raw logs and metrics queryable in the intended stores; retention and disk budgets configured |
| Firewall flows | Fresh retained records from each authorized exporter; correct site filters, sampling/coverage disclosure, top source/destination rankings, and scoped Canvas handoff; collector errors/queues checked |
| JEV | Typed assessment persisted and displayed, shared budget/audit recorded; repeated review reuses assessment; original evidence preserved |

Use repository canaries, documented payloads and the least costly test needed.
Label synthetic records clearly. Verification does not authorize external
notifications, attacks on third-party hosts, an automatic investigation storm,
or new device configuration. Reuse existing authorization and prepare concrete
changes when an additional approval is necessary.

Do not run legacy `smoke-docker.sh` as a read-only check: it renders and starts
services. The Docker helper's `check` and cross-platform `verify.py` default perform GET requests only and
returns a nonzero exit code on failed core checks. It deliberately does not
exercise model spending, webhook writes or packet generation.

Handover includes verified service URLs, deployment command with all overlays,
selected account/runtime, private configuration paths (not values), restart and
rollback steps, telemetry/capture coverage, remaining gaps and next tuning work.
Keep automatic investigations and enforcement separately identified. No claim of
"all VLAN traffic inspected" without evidence at the relevant capture points.

For fresh installations, follow `docs/convergence/verification.md` for the platform evidence matrix. `verify.py --canary` explicitly writes synthetic cases and, on K3s, a synthetic OTLP log. Its saved private case reference lets later checks prove persistence. Record restart, upgrade and restore evidence separately.
