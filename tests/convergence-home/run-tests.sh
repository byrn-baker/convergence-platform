#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
evidence_dir="$(mktemp -d "${TMPDIR:-/tmp}/convergence-home-evidence.XXXXXXXX")"
echo "Private test evidence: $evidence_dir"
exec python3 tests/convergence-home/run-tests.py --evidence-dir "$evidence_dir"
