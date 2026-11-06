# Spec 1007: OTel Convergence Hub

**Status**: Complete (Phase 11 shipped — T145–T156)  
**Mission**: Single OpenTelemetry Collector for all device telemetry (syslog + SNMP)  
**Extracted from**: `080-convergence` US11, Phase 11 (T145–T156)

## What this is

One collector process that:
- **Receives syslog** (RFC3164/5424) on :1514 udp+tcp, parses into structured
  fields at ingest, dual-exports to Loki (14d) + VictoriaLogs (365d)
- **Polls SNMP** per inventory target, exports via prometheusremotewrite to both
  Prometheus (15d, alerting) and VictoriaMetrics (365d, long-term)

Replaces the promtail device-syslog job, syslog-gateway (syslog-ng), and
snmp_exporter. promtail stays for host/agent sources only (journal + OpenClaw
files) — measured, not assumed (T150 decision).

## Scope (in)

- `otel/otel-config.yaml` — syslog receiver, SNMP receivers, log/metric exporters
- Structured log attributes at ingest (facility, severity, hostname, appname, message)
- Cisco IOS regex parser (non-RFC3164 format)
- Device identity from sender IP → message hostname → IP
- Bounded label promotion (FR-042) via `groupbyattrs`
- Dual log export: Loki + VictoriaLogs
- SNMP metric names match Phase 10 recording rules (no rename needed)
- `interface_admin_status` (ifAdminStatus) — distinguishes admin-shut from link-failed
- prometheusremotewrite to Prometheus AND VictoriaMetrics
- Alert rule rewrites for OTel metric names (T153)
- promtail retained for host sources (T150 decision + revisit triggers)
- K3s parity (T156): components/otel-collector, drift guard
- `render-convergence-telemetry.py` OTel section generation (T154)
- `convergence-telemetry-apply.sh` OTel validate-before-restart (T155)

## Scope (out)

- What gets monitored (inventory is 1003)
- Dashboard queries (1006)
- Investigation logic (1001)
- State-history plane (1008)

## Key files

| Path | Role |
|------|------|
| `deploy/convergence/otel/otel-config.yaml` | Collector config |
| `deploy/convergence/otel/probe-snmp-names.yaml` | Metric name verification |
| `deploy/convergence/otel/snmp-receivers.md` | SNMP receiver reference shape |
| `scripts/render-convergence-telemetry.py` | Generates OTel managed sections |
| `scripts/convergence-telemetry-apply.sh` | Validate + apply + restart |
| `deploy/convergence/k8s/components/otel-collector/` | K3s manifest |
| `./decision-record.md` | Decision record |

## Functional requirements (from 080)

FR-036–FR-042

## Success criteria

- SC-017: Same syslog line queryable in both Loki and VictoriaLogs with structured fields
- SC-018: SNMP cutover requires zero query changes to boards/alerts
- SC-019: `interface_admin_status` present; Loki stream count bounded

## Retired components

| What | Replaced by |
|------|-------------|
| syslog-gateway (syslog-ng) | OTel syslog receiver (speaks RFC3164 natively) |
| snmp_exporter | OTel SNMP receivers (emit final metric names directly) |
| device-recording.rules.yml | Unnecessary (OTel produces `interface_*` names) |
| promtail device-syslog job | OTel syslog receiver |

## Tasks (all complete)

T145–T156 (Phase 11).
