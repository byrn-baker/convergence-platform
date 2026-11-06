# Spec 1006: Grafana Board Suite

**Status**: Complete (Phase 10 PR4 shipped — T139–T144)  
**Mission**: Three narrative dashboards that tell operators what matters  
**Extracted from**: `080-convergence` US10, Phase 10 PR4 (T139–T144)

## What this is

Replace scattered single-subject boards with three holistic dashboards — each
telling one NOC story end to end. Every panel must be backed by a collector
installable from this repo, and empty panels must be attributable to "source not
deployed" rather than "healthy."

## The three boards

| Board | UID | Story |
|-------|-----|-------|
| **Network** | `convergence-network` | Site health → WAN → campus switching (named) → Wi‑Fi → edge |
| **Security** | `convergence-security` | Posture → firing alerts → edge/guest → blocks → DNS → auth logs |
| **NetClaw** | `convergence-netclaw` | Token cost → investigation tiers → gateway/mesh logs |

## Scope (in)

- Dashboard JSON provisioning (`grafana/provisioning/dashboards/json/`)
- Legacy board parking (`legacy/` directory, not provisioned)
- Data dependency documentation per board
- Log panel selectors by label (FR-034), never message regex
- Loki ruler recording rules (log-derived metrics → Prometheus via remote-write)
- `smoke-log-panels.sh` validator
- Ingest health alerts (SyslogIngestRefusing, LogExportFailing, etc.)
- Board cross-links in headers

## Scope (out)

- The collectors that feed boards (1003/1007)
- The investigation pipeline (1001)
- The OTel Collector config (1007)

## Key files

| Path | Role |
|------|------|
| `deploy/convergence/grafana/provisioning/dashboards/json/` | Active board JSON |
| `deploy/convergence/grafana/provisioning/dashboards/legacy/` | Parked pilot boards |
| `deploy/convergence/grafana/README.md` | Data dependencies + datasource UIDs |
| `deploy/convergence/loki/rules/` | Loki ruler recording rules |
| `deploy/convergence/smoke-log-panels.sh` | Panel query validator |

## Functional requirements (from 080)

FR-027, FR-030–FR-035

## Success criteria

- SC-014: Exactly three boards provisioned; legacy/ present and unloaded
- SC-015: All panels on Network/NetClaw return data when their collectors are up
- SC-016: Device syslog queryable with structured fields within 5m

## Tasks (all complete)

T139–T144 (Phase 10 PR4).
