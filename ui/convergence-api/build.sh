#!/bin/bash
# Build the API image with an immutable tag (the current commit). Releases are built
# and published by CI (.github/workflows/ci.yml); use this for local or offline installs.
# Usage: ./build.sh [image-name]      default: convergence-api
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
image=${1:-convergence-api}
tag=$(git rev-parse --short=12 HEAD)
docker build -t "$image:$tag" .
echo "$image:$tag"
