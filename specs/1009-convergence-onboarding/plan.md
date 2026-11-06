# Implementation Plan: Convergence onboarding

**Branch**: `1009-convergence-onboarding` · **Date**: 2026-10-30 · [Spec](spec.md)

## Summary
Keep deployment-specific state outside source. Extend the existing stdlib setup
helper with a separate Helm bundle generator and explicit-context checks. Add an
optional namespace-local Grafana to the existing chart; preserve shared Grafana
and all live profiles. Make the user landing page short and route to authoritative
K3s, Docker, operations and troubleshooting guides. Keep skill instructions thin
and reuse those same commands. Prepare a clean upstream scope proposal without
mixing hundreds of unrelated fork commits into a maintainer review.

## Technical Context
Python 3.10+ stdlib CLI; existing Helm/Kubernetes charts and Docker Compose;
PostgreSQL, VictoriaMetrics/Logs, Loki, Grafana; pytest and real disposable-cluster
and Compose checks. Linux is directly available for acceptance; other host and
CPU combinations require explicit evidence before being marked verified.

## Constitution Check
Authorized local fork installation work proceeds. No network-device writes,
existing production cutover, model spending or external notifications are needed.
Credentials remain in private bundles/Secrets; previews and logs exclude them.
No new MCP protocol or service is introduced: this is packaging and setup of
existing components. Existing skill/catalog descriptor hashes and relevant docs
will be reconciled. Upstream substantial scope agreement remains a submission
readiness gate; a design draft is appropriate. Local Git/spec evidence replaces
unavailable GAIT tools for this development session; no GAIT record is claimed.

## Structure and dependencies
- `workspace/skills/convergence-setup/scripts/`: existing Docker helper plus
  isolated K3s bundle generation, target verification and execution commands.
- `deploy/helm/convergence/`: optional standalone Grafana, safe default-storage
  handling and preserved explicit live profile choices.
- `docs/convergence/`: installation, operating and troubleshooting guides.
- `tests/unit/test_convergence_setup*.py`, `deploy/helm/tests/`: generated
  configuration and protection regressions.
- Fresh acceptance uses a disposable Docker-hosted K3s cluster and an independent
  Compose project; no data or credentials from the operator's deployment.

## Design decisions
Kubernetes context and storage class are explicit. Preparation is file-only;
application uses explicit helper lifecycle commands and private credential input. Existing
bundles retain identity and credentials; an existing Secret must agree with the
bundle, never be blindly overwritten. API image delivery is explicit (registry
or all-node import), with platform checks and no default private GHCR dependency.
A normal namespace has its own Grafana; sharing an existing one is an explicit
separate workflow. Network telemetry and investigations remain separate layers.

## Workflow tooling note
The branch hook selected the correct next fork number, 1009. `setup-plan.sh`
rejects four-digit sequential prefixes. The checked-in templates are therefore
followed manually for plan/tasks; no unrelated Spec Kit script change is included.
No new language/runtime technology warrants recreating the fork's intentionally
absent root agent-context file. Upstream proposal numbering is checked separately.
