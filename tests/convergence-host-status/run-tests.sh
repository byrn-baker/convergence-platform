#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# Synthetic operator API/DOM fixtures; never contacts a live receiver or guest.
node --test ui/convergence-api/test-host-status.cjs \
  ui/convergence-api/test-host-heartbeat-ui.cjs \
  ui/convergence-api/test-reliability.cjs
node --test ui/convergence-hud/test/convergence-host-heartbeat.mjs \
  ui/convergence-hud/test/convergence-security-explorer.mjs \
  ui/convergence-hud/test/convergence-security-history.mjs \
  ui/convergence-hud/test/convergence-incident-review.mjs
