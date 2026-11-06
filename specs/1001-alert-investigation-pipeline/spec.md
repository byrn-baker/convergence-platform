# Spec 1001: Alert Investigation Pipeline

**Status**: Complete (Phase 9 shipped)  
**Mission**: Receive alert → decide investigation tier → investigate → report to diary  
**Extracted from**: `080-convergence` US4, US8, Phase 9 (T096–T110)

## What this is

The core NOC automation loop: Alertmanager fires → alert-receiver resolves an
investigation tier (T0/T1/T2) from a versioned policy file → guardian-claw
investigates at the allowed depth → writes findings to the diary → optional
Discord/RAG.

## Scope (in)

- Alert-receiver webhook server
- Investigation policy engine (load, resolve, cache, reload)
- Tier definitions (T0 observe, T1 summarize, T2 multi-tool)
- Budget enforcement (max concurrent T2, max T2/hour, clamp on trip)
- Thin `alert` agent profile (bounded tool set, not the full interactive zoo)
- Fail-safe: missing policy → T0, budget trip → clamp, OpenClaw down → observe plane lives
- Policy file format, seeding, example presets (observe-only, investigate-critical)
- Metrics: `netclaw_investigations_by_tier`, budget trip counters
- Guardian-claw member ensure (idempotent, risk-preserving)
- `investigate=false` label force-to-T0

## Scope (out)

- The alerting rules themselves (those live in 1003/1006/1007)
- The HUD triage UI (that's 1005)
- The feedback loop / RAG write-back (that's 1004)
- Full GUI policy editor (file + CLI is sufficient)

## Key files

| Path | Role |
|------|------|
| `scripts/alert-receiver/` | Webhook server + policy engine |
| `deploy/convergence/config/investigation-policy.example.yaml` | Seed policy |
| `deploy/convergence/config/alert-agent.example.json` | Thin T2 tool profile |
| `scripts/netclaw-investigation-policy.sh` | CLI: show, seed, reload |
| `scripts/ensure-guardian-claw.py` | Idempotent member provisioning |

## Functional requirements (from 080)

FR-009, FR-013–FR-020, FR-005–FR-007

## Success criteria

- SC-007: Default policy (T0) does not start multi-tool investigation
- SC-008: Add one T2 alertname → reloads within ≤60s without code deploy
- SC-009: Budget trip is observable and does not crash the observe plane

## Tasks (all complete)

T096–T110 (Phase 9). See `specs/080-convergence/tasks.md` for details.
