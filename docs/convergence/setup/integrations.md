# Optional integrations

Read repository documents below only for the requested capabilities. Paths are
relative to the checkout; deployed-example inventories are historical examples.

## Victoria, Grafana and retention

Read `deploy/convergence/README.md`, `docker-compose.full.yml` (or the K3s full
component) and `docs/CONVERGENCE-TELEMETRY-ARCHITECTURE.md`. Reuse existing stores
when suitable. Configure active remote-write/export pipelines and prove recent
samples arrive; merely starting VictoriaMetrics/VictoriaLogs does not connect
them. Size disk and retention from observed ingestion, not the example's 365 days.

For a generated Docker bundle, include the full overlay **before the bundle
overlay** so the site's Prometheus and Alertmanager mounts remain authoritative.
Merge desired scrape jobs/recording rules/remote-write into the bundle config.
Inspect the full overlay for optional jobs such as active speed tests and for
additional listening ports. Activate only selected services/profiles and protect
their endpoints according to the intended LAN/proxy access model. Do not assume
the base bundle's loopback binding applies to newly added services.

## Passive IDS and capture coverage

Read `docs/runbooks/convergence-ids.md` for provisioning, ingest authentication,
Suricata, shippers and canaries. Read `convergence-network-coverage.md` when adding
multiple capture points or east-west/same-VLAN visibility. Read
`convergence-ids-victoria.md` when forwarding IDS events/metrics to Victoria.

Discover hypervisor resources, switch mirror capabilities, mirror destinations,
MTU, VLANs/subnets, IPv6, routing and desired boundaries. Use unused VM IDs,
addresses and mirror sessions; verify SSH host keys. Use a dedicated sensor for
the installer that changes NIC MTU/offloads. Review concrete switch changes with
baseline/rollback before applying them within the user's authorization.

A trunk carries frames sent through it, not every frame switched inside every
VLAN. Prove coverage with controlled traffic and captured VLAN/tunnel context.
Preserve unknown VLANs as unknown; endpoint IP ranges do not establish captured
tags. WAN capture can help with perimeter evidence but NAT can hide internal
identity. TLS fingerprints and SNI are metadata, not payload decryption; TLS
interception is a separate explicitly scoped project.

Verify available guest RAM, memory pressure, Suricata memory-cap/drop counters,
NIC drops, rules age, TLS/mTLS trust, shipper lag, collector queues and storage
headroom. Proxmox's host-side memory graph can include cache/VM overhead; do not
resize solely because it appears full. Keep automatic IDS investigations disabled
while establishing noise baselines and tuning.

## External IP context

Read `docs/runbooks/convergence-ip-context.md`. Install the chosen local database,
license/update credentials and refresh mechanism. Verify private endpoints stay
internal and external endpoints show known country/ASN context or explicit
unavailability. Ownership/organization context is not an identity or maliciousness
verdict. Keep provider credentials in local configuration.

## JEV

Read `docs/runbooks/convergence-jev.md` and `mcp-servers/jev-mcp/README.md`. Enable
only when chosen and configured by the operator. Share the JEV ledger and budgets
between Border and Convergence; adapt service write paths to the deployment user.
Keep hosted disclosure controls intact. Test a case review through the HUD and
Guardian's case tool; verify assessment ID, audit, spend and reuse of repeated
requests. JEV's abstract evidence-strength score is not incident probability or
authority to change traffic policy.
## Firewall flows and Diary deep dives

When setting up top talkers or grouped investigations, read
`docs/runbooks/convergence-flow-visibility.md`. Verify actual goflow2 JSON records,
not just collector Prometheus counters. Retain them in VictoriaLogs with a
trusted site label and explicitly configured exporter addresses. Use the
optional K3s manifest renderer when applicable; check JSON integrity, timestamp
freshness, sampling, disk/queue health and same-VLAN capture limits. Confirm
`flow_top_talkers` and `flow_evidence` are discoverable in the configured case MCP.
The new HUD's Diary and Traffic buttons prepare Canvas context; they do not
execute an investigation or change firewall policy on click.
