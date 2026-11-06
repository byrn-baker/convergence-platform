# Research: HUD Home tab

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Evidence reviewed

- [specs/1005-hud-home-tab/spec.md](../../specs/1005-hud-home-tab/spec.md)
- [ui/netclaw-visual/modules/convergence/HomeView.js](../../ui/netclaw-visual/modules/convergence/HomeView.js)
- [ui/netclaw-visual/modules/convergence/tab-router.js](../../ui/netclaw-visual/modules/convergence/tab-router.js)
- [ui/netclaw-visual/modules/convergence/server.js](../../ui/netclaw-visual/modules/convergence/server.js)
- [ui/netclaw-visual/test/convergence-explorer.mjs](../../ui/netclaw-visual/test/convergence-explorer.mjs)
- [specs/080-convergence/hud-polish-backlog.md](../../specs/080-convergence/hud-polish-backlog.md)

## Decision boundaries

The historical HOME view exposes observations through a server-side API proxy
while preserving the main HUD. Its implementation has moved into the Convergence
module. Browser access controls, token secrecy and explicit unavailable states
remain separate from whether the backing services themselves are healthy.

The extracted spec identifies historical task range T010–T017, T020–T026 and H000–H010. The original
[080 task log](../080-convergence/tasks.md) retains its own completion markings.
This reconstruction does not rerun those tasks or convert their claims into new
acceptance evidence. Current commands and platform qualification must be checked
against the current component documentation before execution.
