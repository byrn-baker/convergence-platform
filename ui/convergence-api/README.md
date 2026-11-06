# NetClaw Convergence API (`ui/convergence-api`)

API backend for the Visual HUD **HOME** tab (feature **080-convergence**).

Lifted from `network-guardian-web` (Network Guardian). Primary consumers:

1. HUD `server.js` proxy at `/api/home/*`
2. NetClaw alert-receiver (events POST/PATCH)
3. Optional direct operators

## Dual-run (pilot)

Until Docker/K3s Home deploy lands, the HUD can point at the **existing** pilot:

```bash
# ~/.openclaw/.env or netclaw/.env
HOME_API_URL=https://network-guardian.localedgedatacenter.com
HOME_API_TOKEN=<same as NETWORK_GUARDIAN_TOKEN / API key>
# aliases accepted:
# NETWORK_GUARDIAN_URL / NETWORK_GUARDIAN_TOKEN
```

Local process (optional, needs Prometheus/Postgres reachable):

```bash
cd ui/convergence-api
cp .env.example .env   # fill PROMETHEUS_URL, PG*, API_KEYS, JWT_SECRET
npm install
npm start              # PORT=3080 recommended so it does not collide with HUD :3001
```

## API-first

EJS pages under `views/` remain for legacy/pilot parity; the NetClaw Home product UI is the HUD HOME tab, not these views.

The authenticated Security response includes `hostTelemetry` for the selected
authorized site. It reads the IDS service's existing operator-only host-status
endpoint using server-held `IDS_STATUS_URL` / `IDS_STATUS_TOKEN`; no new credential
or browser access to ingestion is needed. Display fields exclude tenant,
allocation and mapping bindings and all credential material. Unknown, unavailable,
empty, stale and expired observations remain explicit and independent of sensor
and firewall availability. An older receiver without host-status returns an
unavailable heartbeat section without breaking other Security observations.

Both the HUD Security view and the installed dashboard display guest-reported VM
heartbeat freshness, queue/drop health and enrollment expiry. Reporting is
visibility, not a host safety verdict or trusted network capture.

Synthetic API/DOM checks (after `npm ci` in the API and HUD directories):

```bash
bash tests/convergence-host-status/run-tests.sh
```

## Spec

See `specs/080-convergence/`.
