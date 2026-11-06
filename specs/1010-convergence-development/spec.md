# Feature Specification: Convergence development agents and iteration

**Branch:** `1010-convergence-development` · **Created:** 2026-10-30
**Status:** Fork development workflow; separate from the upstream runtime proposal.

## User scenarios and acceptance

### US1 — Delegate Convergence work (P1)
A contributor asks the coding assistant to advance an agreed Convergence task.
The assistant assigns bounded research, implementation and independent verification
work with explicit file ownership. Completion requires relevant check evidence
and coordinator review, not a worker's assertion.

### US2 — Iterate until acceptance or a clear stop (P1)
A contributor requests Ralph-style iteration. The assistant selects one numbered
spec task, records the initial failure or acceptance gap, changes it, verifies it
and re-enters only if evidence supports another attempt within the run budget.
Completion, exhausted budget, repeated unchanged failure and external dependencies
produce explicit final states and a durable next action.

### US3 — Resume without disturbing the installation (P1)
A later coding session reconstructs completed work and open findings from spec
artifacts. This developer workflow does not configure production alert agents,
install a background process or imply authority to merge or deploy.

## Requirements

- FR-001: Discoverable project-local skill under `.agents/skills/`, with one shared
  source for the role contracts and one for the bounded iteration procedure.
- FR-002: Delegate independently useful tasks; one writer per file, coordinator
  owns integration, no recursive delegation or unbounded agent fan-out by default.
- FR-003: Default to three iterations per task and 45 minutes per run, counted
  across delegates. Respect tighter existing user limits. Session instructions
  are not an OS watchdog or a monetary-spend cap.
- FR-004: Record task, acceptance checks, baseline, ownership, elapsed budget,
  findings and outcome in the numbered spec. Missing prerequisites are blocked,
  not passing checks; repeated unchanged failures stop instead of cycling.
- FR-005: A new session reads durable evidence before resuming; prior attempts
  continue to count until the user authorizes a new run budget.
- FR-006: Independent review must exercise realistic success, failure and blocked
  scenarios. Instructions must remain useful when sub-agent tools are unavailable.
- FR-007: Runtime setup stays in `convergence-setup`; this workflow introduces no
  MCP server, runtime skill, model provider, dependency or deployment change.

- FR-008: Review-only requests inspect, check and report within permitted scope.
  They do not implement fixes or change task completion. They write review
  evidence only when a written review artifact is authorized.

## Success criteria

A fresh coding session can select the workflow, delegate a scoped task, use the
correct validation and record a stop/resume decision without relying on chat history.
Behavioral evaluation must show that an unmet acceptance criterion cannot be
reported as complete and that a dependency block does not trigger unrelated work.

## Scope

This is an in-session Ralph-style workflow, not a particular third-party Ralph
plugin or subprocess runner. No root AGENTS.md or global assistant configuration
is introduced. Upstream scope approval remains a separate gate.
