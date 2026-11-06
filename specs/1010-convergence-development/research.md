# Research: development workflow

Inspected the onboarding branch at `4d77458b`. `.agents/skills/` contains the
repository's Spec Kit coding skills. No developer Ralph runner, custom agent
profiles or root AGENTS.md is present. Existing production alert-agent scripts
configure OpenClaw investigations and are inappropriate for development delegation.

Decision: add a coding skill with role contracts and bounded in-session iteration.
Use the coding host's existing delegation tools when available; otherwise execute
sequentially and disclose the absence of independent review. Avoid a new process
runner, model dependency or live runtime configuration for a workflow that can
already execute in this coding session.

Evidence comes from existing spec/task/check artifacts and the repository's test
manifest. Do not duplicate changing test commands into agent role files. A helper
frontmatter check verifies packaging; independent scenario evaluation tests whether
the instructions produce the intended decisions.

Developer-only files are outside `workspace/skills/`, so runtime catalogs, HUD,
SOUL, MCP registration and installer changes are not applicable. The current
onboarding runtime CI does not validate agent behavior; do not claim it does.
