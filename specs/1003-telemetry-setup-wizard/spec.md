# Spec 1003: Telemetry Setup Wizard

**Status**: Complete (Phase 10 shipped)  
**Mission**: Inventory → vendor SNMP templates → apply → named-interface metrics → device config checklist  
**Extracted from**: `080-convergence` US9, Phase 10 (T120–T138)

## What this is

The operator path from "I have switches" to "Prometheus has named-interface
metrics and I know what CLI to paste on each device." Covers inventory
declaration (manual list, Nautobot import, or YAML edit), vendor SNMP module
templates, the render/apply pipeline that writes managed Prometheus sections
idempotently, recording rules for `interface_*` with human names, and the
generated device-side config checklist.

## Scope (in)

- `convergence.yaml` inventory schema (name, IP, vendor/template, role)
- Vendor SNMP module templates: Cisco, pfSense, generic (per-metric lookups)
- `scripts/render-convergence-telemetry.py` (scrape + modules + OTel receivers + checklist)
- `scripts/convergence-telemetry-apply.sh` (managed sections, validate, reload)
- `scripts/convergence-telemetry-setup.sh` wizard (manual | nautobot | netbox | yaml)
- Recording rules `interface_*` (interface_name from ifDescr else ifName)
- Device config markdown generator (Cisco + pfSense SNMP/syslog snippets)
- Nautobot/NetBox inventory import path
- Secrets stay in env (`SNMP_COMMUNITY`), never in committed YAML

## Scope (out)

- The OTel Collector itself (that's 1007)
- Dashboard presentation (that's 1006)
- Alert rules (partially here for device alerts, fully in 1006)
- SuzieQ inventory rendering (that's 1008, but uses the same pipeline)

## Key files

| Path | Role |
|------|------|
| `config/convergence.example.yaml` | Inventory schema + examples |
| `scripts/render-convergence-telemetry.py` | Render pipeline |
| `scripts/convergence-telemetry-apply.sh` | Apply + validate + reload |
| `scripts/convergence-telemetry-setup.sh` | Interactive wizard |
| `scripts/lib/convergence_telemetry_inventory.py` | Nautobot/NetBox import |
| `deploy/convergence/prometheus/alerts/device.rules.yml` | Device alert rules |

## Functional requirements (from 080)

FR-021–FR-029

## Success criteria

- SC-010: Empty targets → wizard → apply → `device_snmp` up + named interfaces within 5m
- SC-011: Nautobot mode writes selection without manual IP typing
- SC-012: Grafana Network board campus switching shows named legends
- SC-013: Generated checklist has syslog host:port + env name (no secret)

## Tasks (all complete)

T120–T138 (Phase 10 PR0–PR3).
