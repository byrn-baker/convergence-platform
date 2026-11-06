# Convergence Spec References

Cross-reference map for finding decision records, contracts, and implementation
details across the spec structure.

## Decision records

| Topic | Canonical location | Working copy |
|-------|-------------------|--------------|
| OTel Collector as ingest hub | `specs/080-convergence/otel-convergence.md` | `specs/1007-otel-convergence-hub/decision-record.md` |
| SuzieQ state-history plane | `specs/080-convergence/suzieq-state-observability.md` | `specs/1008-suzieq-state-plane/decision-record.md` |
| Investigation policy (T0/T1/T2) | `specs/080-convergence/investigation-policy.md` | `specs/1001-alert-investigation-pipeline/decision-record.md` |
| Telemetry setup productization | `specs/080-convergence/telemetry-setup.md` | `specs/1003-telemetry-setup-wizard/decision-record.md` |
| Device telemetry greenfield | `specs/080-convergence/device-telemetry-greenfield.md` | (no copy — 1002 references it) |

## Contracts (API shapes, config schemas)

All remain in `specs/080-convergence/contracts/`:
- `convergence-api.md` — REST API contract
- `adapters.md` — Adapter config shapes (wireless, firewall, SoT, device_telemetry)
- `install-wizard.md` — Installer interaction model
- `investigation-policy.md` — Policy file schema + resolution rules
- `telemetry-setup.md` — Inventory schema, render/apply, managed sections

## Implementation history

- `specs/080-convergence/tasks.md` — Complete task log with checkboxes (T001–T170)
- `specs/080-convergence/data-model.md` — Postgres schema + event lifecycle
- `specs/080-convergence/quickstart.md` — Operator getting-started guide
- `specs/080-convergence/research.md` — Original research notes

## Running system documentation

| Doc | Path |
|-----|------|
| Deploy README | `deploy/convergence/README.md` |
| Troubleshooting | `deploy/convergence/TROUBLESHOOT.md` |
| Grafana board suite | `deploy/convergence/grafana/README.md` |
| Pilot deprecation | `deploy/convergence/DEPRECATION-PILOT.md` |
| Alert safety | `docs/CONVERGENCE-ALERT-SAFETY.md` |
| System overview | `docs/CONVERGENCE.md` |
| Model assignments | `docs/MODELS.md` |
