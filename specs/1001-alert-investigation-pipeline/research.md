# Research: Alert investigation pipeline

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Evidence reviewed

- [specs/1001-alert-investigation-pipeline/spec.md](../../specs/1001-alert-investigation-pipeline/spec.md)
- [services/alert-receiver/server.py](../../services/alert-receiver/server.py)
- [services/alert-receiver/investigation_policy.py](../../services/alert-receiver/investigation_policy.py)
- [services/alert-receiver/test_investigation_policy.py](../../services/alert-receiver/test_investigation_policy.py)
- [deploy/convergence/config/investigation-policy.example.yaml](../../deploy/convergence/config/investigation-policy.example.yaml)
- [specs/080-convergence/investigation-policy.md](../../specs/080-convergence/investigation-policy.md)

## Decision boundaries

The existing policy separates observe-only T0, bounded summaries and multi-tool
investigation. Receiver availability must not depend on an available model. The
old extracted `scripts/alert-receiver/` path is now `services/alert-receiver/`.

The extracted spec identifies historical task range T096–T110. The original
[080 task log](../080-convergence/tasks.md) retains its own completion markings.
This reconstruction does not rerun those tasks or convert their claims into new
acceptance evidence. Current commands and platform qualification must be checked
against the current component documentation before execution.
