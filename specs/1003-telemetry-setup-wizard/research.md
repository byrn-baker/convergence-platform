# Research: Telemetry setup wizard

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Evidence reviewed

- [specs/1003-telemetry-setup-wizard/spec.md](../../specs/1003-telemetry-setup-wizard/spec.md)
- [config/convergence.example.yaml](../../config/convergence.example.yaml)
- [scripts/render-convergence-telemetry.py](../../scripts/render-convergence-telemetry.py)
- [scripts/convergence-telemetry-apply.sh](../../scripts/convergence-telemetry-apply.sh)
- [scripts/convergence-telemetry-setup.sh](../../scripts/convergence-telemetry-setup.sh)
- [scripts/lib/convergence_telemetry_inventory.py](../../scripts/lib/convergence_telemetry_inventory.py)
- [specs/080-convergence/contracts/telemetry-setup.md](../../specs/080-convergence/contracts/telemetry-setup.md)

## Decision boundaries

Inventory is the common input for generated SNMP/OTel sections and the operator
device checklist. Managed-section rendering separates declarative changes from
application; device-side commands remain a distinct authorized operation.

The extracted spec identifies historical task range T120–T138. The original
[080 task log](../080-convergence/tasks.md) retains its own completion markings.
This reconstruction does not rerun those tasks or convert their claims into new
acceptance evidence. Current commands and platform qualification must be checked
against the current component documentation before execution.
