# Spec 1005: HUD Home Tab

**Status**: Complete (Phases 1–2, H shipped)  
**Mission**: Embed network health into the NetClaw Visual HUD as a top-level HOME tab  
**Extracted from**: `080-convergence` US1, US2, Phase 1–2, Phase H

## What this is

The COMMAND | HOME tab split in the Visual HUD. HOME shows Overview (health score,
WAN, Wi-Fi, alerts), Wi-Fi, Devices, Diary, and Triage sub-views — all fetched
from convergence-api via server-side proxy (no browser secrets). COMMAND retains
the Three.js risk/integration scene.

## Scope (in)

- Top-level tab strip (COMMAND | HOME)
- Tab router (show/hide command vs home roots)
- HOME views: Overview, Wi-Fi, Devices, Diary, Triage
- `home.css` using existing HUD design tokens
- `/api/home/*` proxy in HUD server.js → convergence-api
- WebGL canvas pause/resume when HOME active (GPU savings)
- Degraded UI when convergence-api unreachable
- HUD polish items (Phase H): mobile layout, landscape, PWA, accessibility

## Scope (out)

- convergence-api itself (that's 1002)
- What metrics feed the views (that's 1003/1007)
- Three.js scene improvements (separate spec)
- Chat system (already cross-tab, not HOME-specific)

## Key files

| Path | Role |
|------|------|
| `ui/netclaw-visual/index.html` | Tab strip |
| `ui/netclaw-visual/src/app-shell/tab-router.js` | COMMAND/HOME routing |
| `ui/netclaw-visual/src/views/home/HomeView.js` | Home container + sub-nav |
| `ui/netclaw-visual/src/styles/home.css` | HOME styling |
| `ui/netclaw-visual/server.js` | `/api/home/*` proxy |
| `ui/netclaw-visual/src/mobile/` | Mobile layout + PWA |

## Functional requirements (from 080)

FR-001–FR-003

## Success criteria

- SC-001: Tab switch <1s, Command scene stays interactive
- SC-004: Diary events visible in HOME within investigation SLA

## Tasks (all complete)

T010–T017 (Phase 1), T020–T026 (Phase 2), H000–H010 (Phase H).
