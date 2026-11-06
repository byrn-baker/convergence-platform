# Plan: accessible Convergence onboarding

Follow checked-in Spec Kit artifact order manually: requirements, research/design,
tasks, implementation and cross-artifact review. Existing core Docker setup remains
compatible; add an opt-in flow path and companion software-exporter demonstration.
Use existing VictoriaLogs/API response contracts and the real existing HUD renderer.
Prefer existing collector/exporter software over a new protocol implementation.

Coordinator owns spec, acceptance, publication and upstream proposal amendment.
Delegate disjoint implementation and qualification; independently review final diff.
Use only disposable named Docker projects and synthetic/private test artifacts.
Record actual Linux results and unsupported platform boundaries. Prepare a minor
fork version only when the accepted capability is complete. No upstream runtime code
is added by the proposal amendment; no additional upstream tools are proposed.

Artifact coherence: this is an existing Convergence setup capability, not a new
registered MCP server. Update its skill/guide and affected UI/API/tests; unchanged
catalog/registration surfaces must continue to reconcile.
