# Feature Specification: Convergence contribution readiness

**Created:** 2026-10-30 · **Branch:** `1011-convergence-readiness`
**Status:** Authorized fork maintenance and qualification; upstream implementation awaits scope agreement.

## User scenarios and acceptance

### US1 — Review a focused upstream proposal
A maintainer sees a concrete read-only observation/evidence integration with explicit
service ownership, comparison against existing integrations, code/dependency inventory
and bounded acceptance criteria. Existing upstream proposal 161 carries this revision;
this fork spec tracks the parallel readiness work, not approval of upstream scope.

### US2 — Trust repository checks
A contributor can run spec, dependency and affected contract checks from a clean
checkout. Existing failures are reproduced and corrected without exemptions, skipped
assertions or fabricated historical planning. Reconstructed missing artifacts clearly
state their retrospective status and cite existing implementation evidence.

### US3 — Verify a user's path through Convergence
An isolated acceptance exercise traces a known observation through the API and
NetClaw-facing integration to supporting evidence/case behavior. Missing devices,
models and host platforms remain explicit gaps. Existing production services and
credentials remain unchanged. Platform evidence must identify the actual host;
Linux or mocked success cannot qualify macOS Docker Desktop or Windows/WSL2.

## Requirements

- FR-001: Narrow proposal 161 to one useful initial workflow with explicit ownership,
  changed files/dependencies, differentiation and acceptance; exclude automatic
  investigations, case mutations and new observability deployment from first upstream slice.
- FR-002: Reproduce inherited CI failures before fixing. Restore declarations,
  test dependency isolation and fixture fidelity rather than weakening gates.
- FR-003: Historical spec repairs must be honest retrospective records grounded
  in existing files and commits; no claim that design preceded old implementation.
- FR-004: Verify the actual Convergence path using isolated resources and synthetic
  observations; distinguish API, HUD, real telemetry and model/investigation evidence.
- FR-005: Record executed checks, platform limits and remaining external inputs.
- FR-006: Keep live runtime/cluster operations and unrelated features outside this work.

## Success criteria

The revised proposal can be reviewed as a single bounded decision. Reproduced
repository failures either pass after a justified correction or retain an explicit
blocker and reproducer. End-to-end and platform results are independently reviewable,
with no claim that unavailable environments were tested.
