# Convergence HUD views

This source package contains the Convergence site workspace for a NetClaw HUD. Both browser entry points use the same HomeView controller for site selection, health, diary, traffic, security, VM heartbeat and operator actions.

| Package export | Host contract |
| --- | --- |
| `convergence-hud/dashboard` | A React component for a JSX/CSS bundler. The host supplies React 18 and the `preview`, `active`, `refreshKey`, `onStatus` and `investigate` props. Preview uses synthetic content. The investigate callback receives a draft and its evidence for review in Canvas. |
| `convergence-hud/classic` | A browser module exporting `registerUI(ctx)`. The classic host supplies the shared `ctx.state` and its `#app-tabs` navigation element. The entry point mounts the tab, controller and styles. |

## Host integration

The views request same-origin `/api/home/*` endpoints for configuration, authorized sites, telemetry and operator actions. The host must supply an authenticated adapter that verifies the installation, permitted sites and operation for each request and keeps service credentials on the server. The host owns access to its model editor, device graph and terminal capabilities. The device console uses the host's `netclaw:open-terminal` event. [Convergence API contract](../convergence-api/CONTRACT.md)

The platform installer assembles both dashboard entry points from this package. The staging host retains NetClaw's native retained-evidence view and SSH terminal. Its Site operations view uses a site-scoped read adapter and routes case changes to the per-user HTTPS reviewer. [HUD setup and access](../../docs/convergence/setup/hud.md)


## Test the views

From this directory, use Node.js 24.22 or later in the 24.x series.

```sh
npm ci
npm test
```

The tests exercise synthetic API records and DOM interactions, including incorrect-site responses, unavailable data, VM Security and guest-reported heartbeat diagnostics. Production qualification also requires browser checks in both HUDs, owner and site access checks, the model picker, and the Knowledge URL ingestion workflow.

## Source and license

The browser sources and original test suite were extracted from NetClaw's Convergence fork. Geographic outlines retain their Natural Earth public-domain attribution. [Source and license notices](NOTICE) · [Apache License 2.0](LICENSE)

The package also includes a dedicated HTTPS investigation review entry point for individually authenticated reviewers. [Reviewer setup](reviewer/README.md)
