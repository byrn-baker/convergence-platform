#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# Synthetic operator API/DOM fixtures; never contacts a live receiver or guest.
node --test ui/convergence-api/test-host-status.cjs \
  ui/convergence-api/test-host-heartbeat-ui.cjs \
  ui/convergence-api/test-reliability.cjs
node --test ui/netclaw-visual/test/convergence-host-heartbeat.mjs \
  ui/netclaw-visual/test/convergence-security-explorer.mjs \
  ui/netclaw-visual/test/convergence-security-history.mjs \
  ui/netclaw-visual/test/convergence-incident-review.mjs
