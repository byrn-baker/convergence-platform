# Convergence deployment reference

**New installation:** use the [recommended K3s guide](../../docs/convergence/k3s.md) or [local Docker trial](../../docs/convergence/docker.md). The sections below describe legacy/manual profiles and optional adapters; review their site-specific settings before reuse. The trial does not require the `full` profile.

# NetClaw Convergence

Single-host observability + investigation stack for network monitoring.
Receives metrics and logs from network devices, evaluates health via
Prometheus rules, investigates alerts via the NetClaw agent, and presents
findings through the HUD HOME tab and curated Grafana dashboards.

**Project:** `deploy/convergence` · compose name `netclaw-convergence`

For a fresh checkout on another home network, start with the
[Convergence setup skill and guide](../../docs/runbooks/convergence-setup.md).
The generated Docker bundle isolates site configuration and credentials, binds
core ports to loopback, and provides a read-only checker. Legacy examples below
require replacing example addresses and credentials before deployment.

For passive intrusion detection, follow the [Suricata / ERSPAN setup and recovery
runbook](../../docs/runbooks/convergence-ids.md), followed by the
[network-wide capture guide](../../docs/runbooks/convergence-network-coverage.md).
For year-long configured IDS retention and metric trends, enable the
[Victoria stack integration](../../docs/runbooks/convergence-ids-victoria.md).
The guides include a dedicated sensor
VM, authenticated durable ingestion, rule updates, HUD integration and acceptance
tests. This is an optional addition to the existing stack.

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│   Devices (switches, firewall, APs)                                      │
│     syslog (RFC3164) ──▶ OTel Collector ──▶ Loki (14d)                   │
│     SNMP ◀────────────── OTel Collector ──▶ VictoriaLogs (365d)          │
│                                          └──▶ Prometheus (15d)           │
│                                          └──▶ VictoriaMetrics (365d)     │
│                                                                          │
│   Prometheus rules ──▶ Alertmanager ──▶ host alert-receiver              │
│     └── investigation policy (T0/T1/T2) ──▶ NetClaw agent (guardian)     │
│                                                                          │
│   convergence-api ←── HUD HOME tab                                       │
│   Grafana :3300   ←── Operator dashboards (Network · Security · NetClaw) │
└─────────────────────────────────────────────────────────────────────────┘
```

## Services

### Minimal stack (`docker-compose.yml`)

Always-on core. Metrics collection, alerting, health API.

| Service | Port | Role |
|---------|------|------|
| convergence-api | 3080 | Health / wifi / devices / events / inventory API |
| prometheus | 9090 | Metrics + alert rules (15d retention) |
| alertmanager | 9093 | Routes alerts → host alert-receiver |
| blackbox | 9115 | WAN TCP/HTTP probes |
| postgres | internal | Events diary + triage |

Optional profiles on the minimal stack:

| Profile | Service | Role |
|---------|---------|------|
| `unifi` | unifi-exporter (:9899) | UniFi Integration API metrics |
| `generic-snmp-wireless` | snmp-wireless-exporter (:9116) | Non-UniFi wireless SNMP |
| `suzieq` | suzieq-poller + suzieq-rest (:8000) | Historical device state (scale tier, off by default) |

### Full stack overlay (`docker-compose.full.yml`)

Long-term storage, dashboards, structured log ingest, active bandwidth testing.

| Profile | Services | Role |
|---------|----------|------|
| `full` | loki, victoriametrics, grafana, promtail, otel-collector, victorialogs, pushgateway, speedtest | Everything |
| `device-syslog` | otel-collector, victorialogs, promtail | Device telemetry only (no Grafana/VM) |
| `speedtest` | pushgateway, speedtest | WAN bandwidth (Ookla hourly) |

Key services in the full overlay:

| Service | Port | Role |
|---------|------|------|
| **otel-collector** | 1514 (udp+tcp), 8888, 13133 | **Single device telemetry hub** — syslog receiver + SNMP poller |
| victorialogs | 9428 | Structured log store (365d retention) |
| loki | 3100 | Interactive log store (14d retention) |
| victoriametrics | 8428 | Long-term metrics (365d retention) |
| grafana | 3300 | Dashboards (Network · Security · NetClaw) |
| promtail | 9080 | Host/agent logs only (journal + OpenClaw files) |
| pushgateway | 9091 | Speedtest results for Prometheus |

### Retired components

| Component | Replaced by | When |
|-----------|-------------|------|
| syslog-gateway (syslog-ng) | OTel Collector syslog receiver | Phase 11 / T148 |
| snmp_exporter (device SNMP) | OTel Collector SNMP receivers | Phase 11 / T153 |
| device-recording.rules.yml | OTel emits final metric names directly | Phase 11 / T153 |

The `syslog-gateway/` directory is retained as reference but no compose service uses it.

## Quick start

```bash
cd /path/to/netclaw

# 1. Configure
cp deploy/convergence/.env.example deploy/convergence/.env
# Edit: PGPASSWORD, JWT_SECRET, API_KEYS, SNMP_COMMUNITY, ALERT_RECEIVER_URL

# 2. Render templated configs (alertmanager webhook URL)
./deploy/convergence/render-config.sh

# 3. Start minimal
docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env up -d --build

# 4. Start full (adds Grafana, OTel, Loki, VictoriaMetrics/Logs, speedtest)
docker compose \
  -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env \
  --profile full up -d --build

# 5. Smoke test
./deploy/convergence/smoke-docker.sh
```

### Wire the HUD

```bash
# ~/.openclaw/.env (or repo .env)
CONVERGENCE_API_URL=http://127.0.0.1:3080
CONVERGENCE_API_TOKEN=<same as API_KEYS[].key in deploy/convergence/.env>
systemctl --user restart netclaw-hud.service
```

Open http://localhost:3001 → **HOME**.

### Device telemetry setup

For adding SNMP targets (switches) and syslog sources:

```bash
# Interactive wizard (manual entry or Nautobot/NetBox import)
./scripts/convergence-telemetry-setup.sh

# Or edit convergence.yaml directly, then apply
./scripts/convergence-telemetry-apply.sh
```

This renders the OTel Collector config (SNMP receivers + syslog device map),
validates it, and restarts the collector. See `config/convergence.example.yaml`
for the inventory schema.

Device-side setup: after apply, a generated `device-config-snippets.md` gives
per-device SNMP + syslog CLI (no auto-push in v1).

### UniFi metrics

```bash
# deploy/convergence/.env
UNIFI_HOST=https://<controller>
UNIFI_API_KEY=<integration-api-key>

docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env --profile unifi up -d
```

### SuzieQ state plane (optional, scale tier)

For fleets >25 devices or BGP/EVPN/MLAG fabrics where "what was the state
15 minutes before this alert" matters. **Off by default.**

```bash
# deploy/convergence/.env
SUZIEQ_DEVICE_USER=<read-only-service-account>
SUZIEQ_DEVICE_PASSWORD=<...>
SUZIEQ_API_KEY=<rest-api-key>

docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env --profile suzieq up -d
```

Requires SSH access to devices (material credential escalation over SNMP).
See `adapters/suzieq/README.md` for scale guidance and credential model.

## Grafana dashboards

**URL:** http://localhost:3300 (anonymous viewer access)

Three narrative boards in folder **Convergence**:

| Board | UID | Story |
|-------|-----|-------|
| **Network** | `convergence-network` | Site health → WAN → campus switching (named interfaces) → Wi‑Fi → edge |
| **Security** | `convergence-security` | Posture → firing alerts → edge/guest → firewall blocks → DNS → auth logs |
| **NetClaw** | `convergence-netclaw` | Token cost by provider → investigation tiers → gateway/mesh logs |

Legacy/ported boards live under `grafana/provisioning/dashboards/legacy/` (not provisioned).

Every panel is backed by a collector installable from this repo. Board data
dependencies are documented in `grafana/README.md`.

## Alert-receiver integration

Alertmanager posts to `ALERT_RECEIVER_URL` (default
`http://host.docker.internal:8099/webhook`). The investigation policy
(`~/.openclaw/investigation-policy.yaml`) controls whether alerts trigger:

| Tier | Action | Cost |
|------|--------|------|
| T0 | Observe only (diary + Discord) | Free |
| T1 | One-shot summarize (0–1 tools) | Cheap |
| T2 | Multi-tool investigation (allowlisted alerts only) | LLM tokens |

Default posture is T0 (no auto-investigation). Open T2 per-alertname as alert
hygiene improves. See `config/investigation-policy.example.yaml`.

## Installer profile

```bash
./scripts/install.sh --profile convergence
./scripts/setup.sh    # adapters + deploy mode + ensure guardian-claw
```

Setup detects existing iN2N risks, preserves members, ensures a
`guardian-claw` investigator member (idempotent).

## Reusable Helm project stacks

Use the [Helm charts](../helm/README.md) to create isolated VictoriaMetrics /
VictoriaLogs stacks per purpose and register them with the existing shared Grafana.
The `convergence` chart adds the case API/database and network-telemetry services;
its home profile supports parallel validation alongside this Docker deployment.

## K3s deployment

Same services via kustomize under `k8s/`. Namespace `netclaw-convergence`.

```bash
kubectl apply -k deploy/convergence/k8s/overlays/greenfield
./deploy/convergence/k8s/smoke-k8s.sh
```

See `k8s/README.md` for overlay details and `k8s/SMOKE.md` for verification.

## Smoke tests

| Script | What it checks |
|--------|----------------|
| `smoke-docker.sh` | Core services healthy, API responds |
| `smoke-device-snmp.sh` | SNMP metrics with named interfaces in Prometheus |
| `smoke-log-panels.sh` | Every Grafana log panel query is valid LogQL |
| `smoke-telemetry-setup.sh` | Wizard and apply round-trip |
| `smoke-suzieq.sh` | State plane poller + bounded queries |

## File layout

```text
deploy/convergence/
├── docker-compose.yml          # Minimal stack (always-on)
├── docker-compose.full.yml     # Full overlay (Grafana, OTel, Loki, VM, speedtest)
├── .env.example                # All env vars documented
├── render-config.sh            # Template alertmanager config
├── smoke-*.sh                  # Verification scripts
├── config/                     # Investigation policy + alert agent profile examples
├── prometheus/
│   ├── prometheus.yml
│   └── alerts/                 # home.rules.yml, device.rules.yml
├── alertmanager/
├── blackbox/
├── otel/                       # OTel Collector config (syslog + SNMP)
├── loki/                       # Loki config + ruler rules (log-derived metrics)
├── promtail/                   # Host/agent log config (journal + files)
├── grafana/
│   └── provisioning/
│       ├── datasources/
│       └── dashboards/
│           ├── json/           # Active: Network, Security, NetClaw
│           └── legacy/         # Parked pilot boards (not provisioned)
├── adapters/
│   ├── unifi/                  # UniFi Integration API exporter
│   ├── nautobot/               # SoT inventory adapter
│   ├── generic-snmp-wireless/  # Non-UniFi wireless SNMP
│   └── suzieq/                 # State-history plane (optional)
├── speedtest/
├── syslog-gateway/             # RETIRED — reference only (replaced by OTel)
├── k8s/                        # Kustomize base + overlays
├── generated/                  # Rendered configs (gitignored)
└── DEPRECATION-PILOT.md        # Migration path from k3s-observability-stack
```

## Specs

This deploy surface is specified across focused specs in `specs/`:

| Spec | Scope |
|------|-------|
| 1001-alert-investigation-pipeline | Alert receiver → policy → investigation → diary |
| 1002-deployable-obs-stack | Docker/K3s packaging, installer profile |
| 1003-telemetry-setup-wizard | Inventory → templates → apply → named metrics |
| 1004-investigation-feedback-loop | Operator verdicts → RAG → better triage |
| 1005-hud-home-tab | HUD HOME view (Overview, Wi‑Fi, Devices, Diary, Triage) |
| 1006-grafana-board-suite | Three narrative dashboards |
| 1007-otel-convergence-hub | OTel Collector as single device ingest |
| 1008-suzieq-state-plane | Optional historical state queries |

See `specs/1000-convergence-architecture-review/` for the breakdown rationale.

## Troubleshooting

See [TROUBLESHOOT.md](./TROUBLESHOOT.md) for:
- Service health check commands
- Common failure patterns and fixes
- Restart procedures
- Validation scripts

For public source/destination country and ASN attribution, see the
[IP context runbook](../../docs/runbooks/convergence-ip-context.md).
