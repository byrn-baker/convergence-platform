# Plan: Convergence development agents

## Approach

Provide `.agents/skills/convergence-development/SKILL.md`, role and loop references,
and `docs/convergence/development.md` as the human entry point. Link the latter
from the existing Convergence guide. Keep runtime setup unchanged.

A coordinator retains the task and acceptance criteria, assigns at most two
parallel scoped workers, then requests an independent verifier. All delegate
work consumes the same run budget. Durable evidence is stored with the active
numbered spec; transient/private logs stay outside tracked files.

## Validation

Validate skill frontmatter, local links and whitespace; inspect all required
spec artifacts. Independently forward-test the workflow against representative
coding tasks and a resume case. Correct observed decision errors and repeat only
the affected scenarios. No runtime tests are needed for instruction-only changes.

## Compatibility and release

Branch from the reviewed onboarding work and use a separate stacked fork PR so
upstream's design-only proposal remains focused. Merge preparation adds source version 1.15.0, changelog and release notes for
the new development skill; tagging/publication is separate.
No global assistant configuration, live checkout, cluster or runtime changes.
