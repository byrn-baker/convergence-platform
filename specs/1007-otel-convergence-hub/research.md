# Research: OTel Convergence hub

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Evidence reviewed

- [specs/1007-otel-convergence-hub/spec.md](../../specs/1007-otel-convergence-hub/spec.md)
- [deploy/convergence/otel/otel-config.yaml](../../deploy/convergence/otel/otel-config.yaml)
- [scripts/render-convergence-telemetry.py](../../scripts/render-convergence-telemetry.py)
- [scripts/convergence-telemetry-apply.sh](../../scripts/convergence-telemetry-apply.sh)
- [deploy/convergence/k8s/components/otel-collector](../../deploy/convergence/k8s/components/otel-collector)
- [specs/1007-otel-convergence-hub/decision-record.md](../../specs/1007-otel-convergence-hub/decision-record.md)

## Decision boundaries

The accepted decision record separates device syslog/SNMP ingestion from retained
host-log sources. It preserves metric naming and bounded label promotion while
exporting to distinct interactive and long-retention stores. Historical measured
results apply to their recorded environment, not every deployment.

The extracted spec identifies historical task range T145–T156. The original
[080 task log](../080-convergence/tasks.md) retains its own completion markings.
This reconstruction does not rerun those tasks or convert their claims into new
acceptance evidence. Current commands and platform qualification must be checked
against the current component documentation before execution.
