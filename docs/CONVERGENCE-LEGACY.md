# Historical Convergence deployment notes

This is an archived engineering reference, not an installation guide. Commands, host addresses and status claims below describe earlier deployments. For new installations use [Convergence](CONVERGENCE.md). Do not copy teardown or migration commands without a reviewed backup and cutover plan.

# NetClaw Convergence

**Convergence** is the site operations product path inside NetClaw: HUD **HOME**
tab, metrics stack, device adapters, investigation diary, and the
Alertmanager → alert-receiver → guardian-claw loop.

**New installation:** use the bundled [`convergence-setup` skill](../workspace/skills/convergence-setup/SKILL.md)
and [fresh-install guide](runbooks/convergence-setup.md). They start from your
machine and network, with private generated credentials and separate checks for
core services, actual telemetry, the HUD and investigations.

| Spec | Code | Deploy |
|------|------|--------|
| [`specs/1001–1008`](../specs/1000-convergence-architecture-review/plan.md) | [`ui/convergence-api/`](../ui/convergence-api/) · HUD HOME | [`deploy/convergence/`](../deploy/convergence/) |
| Tasks | T070–T073 complete (SoT, SNMP wireless, full OBS, k8s components) | [quickstart](../specs/080-convergence/quickstart.md) |

**Not renamed:** `guardian-claw` (iN2N investigator identity).
**Legacy pilot:** external `network-guardian-web` in k3s-observability-stack (dual-run via env aliases).

### Models (brain vs alert triage)

Set once in **repo `.env`**, apply with script or HUD:

| SoT | Apply |
|-----|--------|
| `NETCLAW_BRAIN_MODEL` | Interactive Border / chat |
| `NETCLAW_ALERT_TRIAGE_MODEL` | T2 investigation hooks |

```bash
./scripts/netclaw-apply-models.sh show|apply|preset split
# HUD: Convergence → Models → Apply & restart gateway
```

Full operator guide: [`docs/MODELS.md`](./MODELS.md) · env layout: [`ENV-AND-LAYOUT.md`](./ENV-AND-LAYOUT.md).

Optional JEV second opinions for IDS cases are available in Diary, Triage and
Guardian's case tools. See the [JEV review runbook](runbooks/convergence-jev.md)
for setup, data sharing, shared budgets and recovery behavior.

Security review priorities and local ISP incident/email evidence drafts are
documented in [Incident review and provider reports](runbooks/convergence-incident-review.md).
For grouped Diary investigations, per-case Canvas handoffs, and source/destination
traffic rankings from firewall flow exports, see
[Diary and flow visibility](runbooks/convergence-flow-visibility.md).

---

## Planes (where things run)

```
┌─────────────────────────────────────────────────────────────┐
│  Agent plane (host)                                         │
│  • OpenClaw gateway + Border                                │
│  • guardian-claw member                                     │
│  • HUD :3001 (ui/netclaw-visual)                            │
│  • alert-receiver :8099  →  services/alert-receiver/        │
│  • Config: ~/.openclaw/.env  (or repo .env)                 │
└───────────────────────────┬─────────────────────────────────┘
                            │ HOME tab /api/home/* proxy
                            │ AM webhook / reinvestigate
┌───────────────────────────▼─────────────────────────────────┐
│  Stack plane (Docker Compose or K3s)                        │
│  • convergence-api :3080 (service name + image)             │
│  • postgres, prometheus, alertmanager, blackbox             │
│  • optional: unifi-exporter, snmp-wireless, full-stack      │
│  • Config: deploy/convergence/.env                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Telemetry architecture

See **[`CONVERGENCE-TELEMETRY-ARCHITECTURE.md`](./CONVERGENCE-TELEMETRY-ARCHITECTURE.md)** for:

- System diagram (OTel Collector → Loki/VictoriaLogs + Prometheus/VictoriaMetrics)
- What gets structured at ingest (filterlog CSV, Cisco mnemonics, RFC3164 fields)
- Label cardinality rules (FR-042)
- Metric names, log-derived metrics, alert rules
- Retention tiers (14d interactive, 365d long-term)
- Inventory and setup flow (Nautobot or manual → apply → boards)
- Key decisions with rationale
- Phase 12: RAG-driven vendor profiles (new platform = one markdown doc)

---

## Quick start — local Docker (primary path)

Same host runs: agent plane + compose stack + alert-receiver.

```bash
# 1) Install profile (once)
./scripts/install.sh --profile convergence && ./scripts/setup.sh

# 2) Stack env
cp -n deploy/convergence/.env.example deploy/convergence/.env
# Edit: API_KEYS[].key, PGPASSWORD, JWT_SECRET, UNIFI_* if using Wi‑Fi metrics

# 3) Agent plane (~/.openclaw/.env) — match token to API_KEYS
#   CONVERGENCE_API_URL=http://127.0.0.1:3080
#   CONVERGENCE_API_TOKEN=<same as API_KEYS[].key>

# 4) Alert receiver (host systemd) — diary URL = local API
# services/alert-receiver/.env:
#   NETWORK_GUARDIAN_URL=http://127.0.0.1:3080
#   NETWORK_GUARDIAN_TOKEN=<same key>
#   OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
#   OPENCLAW_HOOK_TOKEN=<gateway hook token>
sudo cp scripts/systemd/netclaw-alert-receiver.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now netclaw-alert-receiver
curl -fsS http://127.0.0.1:8099/health

# 5) Bring stack up (optional --profile unifi)
cd deploy/convergence
./render-config.sh    # writes AM webhook → host.docker.internal:8099
docker compose -f docker-compose.yml --env-file .env --profile unifi up -d --build
./smoke-docker.sh

# 6) HUD
systemctl --user restart netclaw-hud.service
# Open http://127.0.0.1:3001 → HOME  (status: /api/home/status → configured:true)
```

**Verified path:** Alertmanager → `host.docker.internal:8099/webhook` → alert-receiver
→ gateway/investigation → POST diary on `convergence-api` (`/api/events`).

Full OBS overlay: `docker-compose.full.yml` + `--profile full`

### Tear down old `netclaw-home` stack

```bash
docker compose -p netclaw-home down --remove-orphans
docker volume rm netclaw-home_home-amdata netclaw-home_home-pgdata netclaw-home_home-promdata 2>/dev/null || true
```

---

## Flip Docker → K3s (same agent plane)

Agent plane (HUD, alert-receiver, OpenClaw) stays on the host. Only the **stack**
moves into the cluster.

| | Docker local | K3s |
|--|--------------|-----|
| Namespace / project | compose `netclaw-convergence` | K8s NS `netclaw-convergence` |
| API reachability | `http://127.0.0.1:3080` | NodePort **30080** or port-forward |
| AM → webhook | `host.docker.internal:8099` | Host IP or NodePort to alert-receiver (e.g. `http://192.168.3.252:8099/webhook`) |
| Agent env | `CONVERGENCE_API_URL=http://127.0.0.1:3080` | `CONVERGENCE_API_URL=http://<node>:30080` |

```bash
# A) Stop Docker stack (optional — free ports)
cd deploy/convergence
docker compose -f docker-compose.yml --env-file .env --profile unifi down

# B) Secrets + apply
cp deploy/convergence/k8s/secret.example.yaml /tmp/convergence-secret.yaml
# fill API_KEYS, PGPASSWORD, JWT, UNIFI_API_KEY
kubectl apply -f /tmp/convergence-secret.yaml
kubectl apply -k deploy/convergence/k8s/overlays/greenfield
# or greenfield-full for Loki/VM/Grafana components

# C) Point agent plane at NodePort
# ~/.openclaw/.env  CONVERGENCE_API_URL=http://127.0.0.1:30080
# services/alert-receiver/.env  NETWORK_GUARDIAN_URL=http://127.0.0.1:30080
sudo systemctl restart netclaw-alert-receiver
systemctl --user restart netclaw-hud.service

# D) Patch Alertmanager webhook if not using host.docker.internal
# In secret/config: ALERT_RECEIVER_URL=http://<host-LAN-IP>:8099/webhook
```

Smoke: `deploy/convergence/k8s/SMOKE.md` · offline `./deploy/convergence/k8s/smoke-k8s.sh`

**Do not** deploy into the pilot `observability` namespace (legacy Network Guardian).

---

## Alert path (must not be “standalone forever”)

```
Prometheus rules → Alertmanager → services/alert-receiver :8099/webhook
       → OpenClaw/Border → guardian-claw (alert-triage skill)
       → POST/PATCH convergence-api /api/events  (diary)
       → optional Discord + RAG snapshot
```

Triage **Need More** → convergence-api → `ALERT_RECEIVER_URL` `/reinvestigate`
(set in `deploy/convergence/.env` to `http://host.docker.internal:8099/webhook`).

Details: `services/alert-receiver/README.md`, skill `workspace/skills/alert-triage/`.

---

## Knowledge / UniFi API docs

Vendor manuals go in RAG (`~/.openclaw/rag`), not the live Integration API:

- OpenAPI: `https://developer.ui.com/network/v{version}/openapi.json` (type **vendor**)
- Runbook: [knowledge-rag-home-ops.md](./runbooks/knowledge-rag-home-ops.md)

---

## Naming cheat sheet

| Concept | Name |
|---------|------|
| Product / paths | **Convergence** |
| HUD top tab | **CONVERGENCE** (internal route id remains `home`) |
| Docker/K8s service | **`convergence-api`** |
| Image | `netclaw-convergence-api:local` |
| Agent env (preferred) | `CONVERGENCE_API_URL` / `CONVERGENCE_API_TOKEN` |
| Aliases | `HOME_API_*`, `NETWORK_GUARDIAN_*` |
| Investigator claw | **guardian-claw** (unchanged) |
| Compose project / K8s NS | `netclaw-convergence` |

---

## Related docs

- [ENV-AND-LAYOUT.md](./ENV-AND-LAYOUT.md) — where secrets live; do not scatter `.env` copies
- [deploy/convergence/README.md](../deploy/convergence/README.md)
- [specs/080-convergence/quickstart.md](../specs/080-convergence/quickstart.md)
- [services/alert-receiver/README.md](../services/alert-receiver/README.md)
- **Greenfield PR (not built yet):** campus switch SNMP + NetClaw agent
  metrics/logs as optional components —
  [`specs/080-convergence/device-telemetry-greenfield.md`](../specs/080-convergence/device-telemetry-greenfield.md)
  (distinct from optional AP-only `generic-snmp-wireless`)

### Phase 8 greenfield status

| Capability | Status | How to enable |
|------------|--------|----------------|
| Campus switch IF-MIB SNMP | **Shipped** | `--profile device-snmp` / K3s `device-snmp` |
| Device syslog → Loki | **Shipped** | `--profile full` + promtail (UDP 1514) |
| K3s syslog component | **Shipped** | `k8s/components/device-syslog` (T091) |
| HOME Devices + Overview SNMP KPI | **Shipped** | `ifOperStatus{job="device_snmp"}` (T087) |
| Setup wizard SNMP/syslog prompts | **Shipped** | when `convergence-device-snmp` / `-syslog` selected (T083) |
| NetClaw token/cost metrics | **Shipped** | `openclaw-token-exporter` + job `netclaw-openclaw` |
| NetClaw agent log ship | **Template** | `scripts/rsyslog-netclaw-convergence.conf` |
| Grafana NetClaw quota board | **Shipped** | `--profile full` provisions dashboards |
| Network switch Grafana board | **Shipped** | `device-snmp-switches.json` |
| Alert → investigation safety rails | **Shipped** | [CONVERGENCE-ALERT-SAFETY.md](./CONVERGENCE-ALERT-SAFETY.md) |


## Reliability and security foundation — 24 October 2026

The receiver now requires `Authorization: Bearer $ALERT_RECEIVER_TOKEN` for
mutation endpoints. Nautobot retains its separate signed-webhook verification.
Use the same receiver token in the receiver and Docker environments; run
`deploy/convergence/render-config.sh` to create the private Alertmanager credential
file. Do not commit `deploy/convergence/secrets/`. Recreate Alertmanager and the
API after credential changes. K3s deployments must provision the equivalent
credential Secret and client header before upgrading their receiver.

Alerts are persisted in `services/alert-receiver/state/jobs.sqlite` before HTTP
acknowledgement. The bounded queue has 1,000 pending entries, two investigation
workers by default, a separate ingest/recovery worker, and a learning worker.
Model work cannot occupy the ingest worker. Repeated deliveries
of the same fingerprint/start/status are idempotent. Case creation also has a
unique site/incident key. Checkpointed dispatches survive restart and resume
watching the original case; an uncertain HTTP response does not blindly launch
another model run. Completion has a 900-second default deadline; overdue cases
are escalated. This is case-level completion accounting, not proof that every
underlying model subprocess has terminated. Queue retries are bounded to three
attempts. `/health` exposes queued/running/done/failed counts.

Manual follow-ups use the same queue and hourly dispatch budget. Automatic
allowlists match current `WanHardDown`, warning `SwitchLinkLost`, and
`DeviceSnmpStale` rules. Site-level alerts need no fabricated device IP. OTel
interface metrics replace retired exporter queries, and missing Alertmanager
counts are unknown rather than zero. The SNMP disappearance rule retains a
24-hour inventory window; explicit inventory-based absence detection remains
necessary for devices silent longer than that or never observed.

Case GET/PATCH/reinvestigation routes enforce site scope. Mutations require an
admin/operator role. PostgreSQL retains previous event versions in
`event_history`; `alert_cleared_at` is separate from root cause and investigation
notes. Existing databases are migrated additively at API startup. Back up both
PostgreSQL and the receiver queue for recovery.

Case evidence/feedback updates queue learning. Snapshots use a content hash;
corrected snapshots are retained as superseded history and excluded from the
investigation search alias. Failed learning is visible in receiver job state;
there is not yet a transactional outbox spanning the case database and receiver.

Guardian's deployed main-agent tool allowlist is recorded in
`deploy/convergence/config/guardian-readonly-tools.json`. It excludes shell,
configuration, arbitrary HTTP and code execution tools. The dedicated
`mcp-servers/convergence-case-mcp/server.py` exposes only case read/update through
a configured API and site. Run it using the existing fastmcp4 Python environment
and register it as `convergence-case-mcp` in the member configuration. Device
credentials should additionally be replaced with read-only service accounts;
tool filtering is not a substitute for device-side authorization.
Native file reads are limited to the agent workspace. Code Mode's JavaScript
`exec`/`wait` discovery wrappers remain available; these are distinct from a
shell execution tool and expose the filtered tool catalog.

The Border alert coordinator uses `config/alert-coordinator-tools.json` (relative
to `deploy/convergence`): delegation, task observation and case updates. Its
autonomous profile excludes direct device configuration and mesh administration.
Guardian uses a dedicated workspace, with `config/guardian-instructions.md` as
`AGENTS.md` and copies of alert-triage, pyats-network, pyats-troubleshoot,
prometheus-monitoring, grafana-observability and pfsense-threat-intel skills.
Refresh these copies when updating the repository's investigation instructions.
Every MCP environment reference must be explicitly present in the member's
private environment. Register the case MCP with `CONVERGENCE_API_URL` and
`CONVERGENCE_API_TOKEN` references to that member's case API credentials.

Convergence now includes a Security view with structured firewall block totals,
source rankings and explicit sensor coverage. Firewall blocks are unclassified
observations, not confirmed intrusions. The optional [passive Suricata feed](runbooks/convergence-ids.md)
adds authenticated EVE ingestion, durable delivery, validated rule updates,
original case evidence and per-VLAN sensor health. The
[network coverage extension](runbooks/convergence-network-coverage.md) adds
three-switch endpoint ingress capture, independent mirror-source liveness,
Flannel VXLAN decoding and TLS metadata visibility with explicit coverage gaps.
Network-zone/asset correlation,
authenticated HUD ingress, restricted
backend exposure, dependency updates, and restore/failure exercises remain work
before claiming enterprise IDS capability. Cloud model availability is not a
requirement for recording alerts or viewing telemetry.

The Convergence API image now uses Node 24 and refreshed locked dependencies,
including bcrypt 6. The production npm audit reports zero known advisories at
this change; this is not a security assessment of every service in the stack.

Verification:

```sh
.venv/bin/python -m pytest services/alert-receiver/test_reliability.py
node --test ui/convergence-api/test-reliability.cjs
promtool test rules services/alert-receiver/test_snmp_rules.yml
.venv/bin/python -m pytest tests/contract/test_convergence_api.py
```

### IDS long-term telemetry

The optional [Victoria integration](runbooks/convergence-ids-victoria.md) sends
structured EVE observations to VictoriaLogs with independent durable delivery
and bounded replay of retained history. OTel exports IDS capture health and
alert-rate metrics to both Prometheus and VictoriaMetrics. Grafana includes
**IDS Capture and Alerts** for reviewing the baseline; automatic IDS
investigations remain disabled during tuning.
