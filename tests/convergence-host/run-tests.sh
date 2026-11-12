#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# Fixtures only: never discovers or contacts a guest, hypervisor or live backend.
export PYTHONPATH="$PWD/services/ids-ingest${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pytest services/ids-ingest deploy/convergence/host-agent tests/convergence-host -q
# These legacy services both name their module server.py; use a fresh interpreter, run
# from the receiver's directory as CI does. Not PYTHONPATH: the RAG worker subprocess
# inherits it and refuses to run with the receiver importable.
unset PYTHONPATH
cd services/alert-receiver
exec python3 -m pytest -q
