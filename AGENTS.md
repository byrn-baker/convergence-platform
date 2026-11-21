# Convergence stabilization

New features are frozen, except those the owner exempts in writing. The only exemption is
spec 1018 (identity hub), granted 2026-11-21; its own spec, plan and tasks govern it, and
its live changes need owner approval. For compatibility, installation, recovery,
maintenance or qualification work, read `specs/1017-production-stabilization/spec.md`, `plan.md`,
`tasks.md` and the current run in `verification.md` before acting. Preserve running
production and existing work; record actual evidence before completing a task.

Build modularly with clear ownership. If the natural solution needs a substantial
architectural refactor, explain it before implementation.

Use an available authenticated Grok Build subscription for bounded documentation,
source inventory, test drafting, mechanical fixes and secondary review when the
task has explicit inputs, allowed files and acceptance checks. Follow
`docs/development/agent-delegation.md`. The coordinator validates results and owns
architecture, production actions and final acceptance. Grok availability is not a
prerequisite for the software or CI.
