# Research: readiness baseline

Baseline `09748c37` includes onboarding and coding-workflow PRs. Hosted main jobs
now completed: release metadata and HUD pass; MCP Reconciliation fails. Earlier
reports identify 46 missing historical spec artifacts, seven FastMCP declaration
findings, a startup-fixture error and missing unit-runner zstandard dependency.
Reproduce exact current failures; prior reports are hypotheses, not proof.

Upstream proposal 309 has no maintainer comments. Its current broad deployment/API/
HUD/IDS roadmap needs a recommended service boundary and smaller first read-only
observation/evidence integration. Compare actual upstream files before finalizing.

Linux x86_64 is available locally and native ARM64 in CI. Existing onboarding
qualification covers core services, persistence and canaries, not a complete
NetClaw investigation or actual macOS/WSL host behavior. Platform access is pending.
