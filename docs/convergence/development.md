# Developing Convergence with sub-agents

Use the repository's [convergence-development coding skill](../../.agents/skills/convergence-development/SKILL.md)
when advancing Convergence features, fixes, documentation or upstream acceptance.
It follows NetClaw's numbered `specs/` workflow and delegates bounded research,
implementation and independent verification. Installation and operations use the
separate [convergence-setup skill](../../workspace/skills/convergence-setup/SKILL.md).

## Start a coding session

Open a development checkout containing `.agents/skills/convergence-development/`.
Coding hosts that discover `.agents/skills/` can select the skill automatically.
In any host that can read repository files, this explicit prompt is sufficient:

> Read `.agents/skills/convergence-development/SKILL.md`. Advance the next ready
> task in `specs/<active-spec>/tasks.md`. Use sub-agents where they help and the
> bounded Ralph-style loop. Record verification and stop conditions in that spec.

Replace `<active-spec>` with the agreed numbered spec. For a new feature, prepare
its spec, research, plan and tasks before implementation. The
[workflow's own spec](../../specs/1010-convergence-development/spec.md) describes
this developer capability; it is not the backlog for all Convergence work.

For review without implementation:

> Use the Convergence development skill to review this branch against its spec.
> Delegate independent checks. Report findings and coverage gaps without editing
> implementation or deploying anything.

For resume:

> Resume the Convergence development run recorded in the active spec's
> `verification.md`. Preserve its attempt counts and remaining budget; identify
> any prerequisite that still prevents progress.

## How the team works

The coordinator keeps ownership of scope, integration and completion. A researcher
answers an uncertainty, an implementer edits assigned files, and an independent
verifier checks the integrated result. At most two workers run concurrently;
verification follows integration. One writer owns each file. If the coding host
cannot spawn agents, the work runs sequentially and reports that review limitation.

## What the loop does

The assistant repeatedly selects one task, records the baseline, implements,
checks acceptance and reviews the result. Defaults are three attempts per task
and 45 minutes for the whole run. All agents share that budget. It stops when
acceptance passes, the budget expires, the same failure repeats without new
evidence, or required access/decisions are unavailable. Progress and next actions
survive session interruptions in the numbered spec's task and verification files.

These are session instructions, not a hard process timeout or a spending cap.
This setup does not install a third-party Ralph plugin, launch background model
processes, change global coding-assistant settings or configure NetClaw's production
alert agents. It does not bypass the upstream maintainer's scope decision. Changes
that are ready for review remain separate from live deployments.
