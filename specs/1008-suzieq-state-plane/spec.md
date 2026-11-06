# Spec 1008: SuzieQ State-History Plane

**Status**: In progress (T158–T165 complete; T166–T170 open)  
**Mission**: Optional scale-tier historical state queries for investigations  
**Extracted from**: `080-convergence` US12, Phase 12 (T158–T170)

## What this is

An optional component for larger fleets (>25 devices, or any BGP/EVPN/MLAG
fabric) that answers "what was the BGP/route/MAC/ARP/LLDP state before this
alert fired" without SSHing to devices at investigation time. Off by default.
Complements but does not replace the OTel metrics/logs planes.

## Scope (in)

- SuzieQ poller + REST API (Docker profile `suzieq`)
- Inventory render from existing `device_telemetry.snmp.targets` (no second inventory)
- MCP server hardening: row cap, byte ceiling, per-table column defaults, truncation metadata, freshness stamps
- Poller health alerts (SuzieQPollerDown, SuzieQPollerStale)
- Credential model: env/secret refs only, dedicated read-only service account, per-namespace separation
- Scale guidance documentation
- Smoke test (bounded queries, SNMP path asserted off)
- PROFILE_RECOMMENDED fix (don't advertise tools without a server)
- K3s component (T166 — open)
- Agent wiring: thin alert profile integration (T167–T168 — open)
- Wizard + installer: catalog component with credential disclosure (T169 — open)
- Assert/change validation documentation (T170 — open)

## Scope (out)

- The OTel Collector (1007)
- Alert rules (1001/1006)
- Dashboards (1006 — state plane has no provisioned board per FR-043)
- Interactive pyATS investigation (separate concern)

## Key files

| Path | Role |
|------|------|
| `deploy/convergence/adapters/suzieq/` | Docker adapter (README, inventory, config templates) |
| `deploy/convergence/smoke-suzieq.sh` | Smoke test |
| `mcp-servers/suzieq-mcp/` | MCP server (hardened, row-capped, freshness stamps) |
| `workspace/skills/suzieq-observability` | Bounded query skill |
| `./decision-record.md` | Decision record |

## Functional requirements (from 080)

FR-043–FR-050

## Success criteria

- SC-020: Oversized query returns truncated response with explicit metadata
- SC-021: Enabling adds zero new inventory files (renders from existing targets)
- SC-022: Poller stopped → staleness alert fires within window
- SC-023: T1-eligible alert gets bounded state context within token ceilings

## Open tasks

- T166: K3s component (Deployment, PVC, Secret, Service)
- T167: Add `suzieq-mcp__*` to alert agent thin profile
- T168: Rewrite skill to enforce bounded queries (relative start_time, view=changes, explicit columns)
- T169: Catalog component with credential disclosure + scale guidance
- T170: Document assert/change validation workflow

## Completed tasks

T158–T165.

## Vendor coverage (measured T160)

| Vendor | Status |
|--------|--------|
| Cisco IOS-XE (Catalyst 9300) | Full — all tables populated |
| pfSense / FreeBSD | Unsupported — firewall stays metrics/logs only |

## Upstream health

SuzieQ 0.24.0 pinned (last release May 2025). Differentiated work moves to
SuzieQ Enterprise. Accepted risk documented.
