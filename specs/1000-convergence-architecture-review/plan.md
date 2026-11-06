# Plan: Convergence Architecture Review

**Status**: Executed — spec 080 decomposed into 1001–1008  
**Date**: 2026-08-20

## Spec Breakdown (1000-series, fork-local)

The monolithic spec 080-convergence has been decomposed into focused,
mission-aligned specs. Each owns a clear slice of the NOC pipeline.

| Spec | Name | Mission Alignment | Status |
|------|------|-------------------|--------|
| 1000 | convergence-architecture-review | Meta: this review + restructuring | Done |
| 1001 | alert-investigation-pipeline | Core NOC: receive alert → investigate → report | Complete |
| 1002 | deployable-obs-stack | Core NOC: installable metrics/logs collection | Complete |
| 1003 | telemetry-setup-wizard | Core NOC: inventory → exporters → dashboards | Complete |
| 1004 | investigation-feedback-loop | NOC improvement: operator verdicts → RAG → better investigations | Complete |
| 1005 | hud-home-tab | UI: Home view in the Visual HUD | Complete |
| 1006 | grafana-board-suite | Ops: three narrative dashboards | Complete |
| 1007 | otel-convergence-hub | Infra: single collector for syslog + SNMP | Complete |
| 1008 | suzieq-state-plane | Scale: historical state queries for investigations | In progress (T166–T170 open) |

## Congruence Filter

**Belongs in the NOC mission** (monitor + investigate + report):
- Alert receiver + investigation policy (T0/T1/T2 tiering) → 1001
- Device telemetry collection (SNMP, syslog) → 1003, 1007
- Investigation feedback loop (operator verdicts improve future triage) → 1004
- Network Guardian dashboard (presenting findings) → 1005, 1006

**Adjacent infrastructure** (enables the NOC, separate deliverables):
- Docker/K8s deploy scaffolding → 1002
- OTel hub (plumbing) → 1007
- Grafana board suite (operator tooling) → 1006
- SuzieQ state plane (scale enhancement) → 1008

**Explicitly NOT sprawl** (verified congruent):
- HUD HOME tab is the operator's entry point → 1005
- Three boards replace 13 scattered ones → 1006

## Archive

Spec `080-convergence` is superseded by this breakdown. Its `spec.md`, `tasks.md`,
`plan.md`, and decision records (`otel-convergence.md`, `investigation-policy.md`,
`telemetry-setup.md`, `suzieq-state-observability.md`) are retained as the
authoritative history. New work references 1001–1008 specs.

## What's left to build

Only spec 1008 has open tasks (T166–T170). These are all **optional scale-tier**
work that should not be prioritized unless operator demand arrives:

- T166: K3s component for SuzieQ
- T167–T168: Wire state plane into the alert agent profile
- T169: Installer catalog entry with credential disclosure
- T170: Assert/change validation documentation

Everything else in Convergence is shipped and running.

This status describes the original 1001–1008 specification scope. Later IDS,
security-review and provider-reporting work has additional operational readiness
tasks; see [remaining operational work](../../docs/runbooks/convergence-incident-review.md#remaining-operational-work)
before treating the current installation as complete.
