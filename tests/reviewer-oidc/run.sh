#!/usr/bin/env bash
# Starts a disposable Keycloak at the version production pins, runs the sign-in
# qualification against it, and removes it. Needs Docker, Node 24 and openssl.
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
: "${REVIEWER_TEST_DATABASE_URL:?Set this to a disposable PostgreSQL 16 test database}"
image="${KEYCLOAK_IMAGE:-quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332}"
port="${KEYCLOAK_PORT:-18443}"
export KEYCLOAK_CONTAINER="reviewer-oidc-keycloak-$$"
export KEYCLOAK_URL="http://127.0.0.1:$port"
export KEYCLOAK_ADMIN_PASSWORD
KEYCLOAK_ADMIN_PASSWORD=$(openssl rand -hex 24)
trap 'docker rm -f "$KEYCLOAK_CONTAINER" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$KEYCLOAK_CONTAINER" -p "127.0.0.1:$port:8080" \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=fixture-admin -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KEYCLOAK_ADMIN_PASSWORD" \
  "$image" start-dev >/dev/null
for _ in $(seq 1 90); do
  if curl -fsS -o /dev/null "$KEYCLOAK_URL/realms/master/.well-known/openid-configuration" 2>/dev/null; then ready=1; break; fi
  sleep 2
done
test "${ready:-}" = 1 || { echo "Keycloak did not become ready." >&2; docker logs --tail 20 "$KEYCLOAK_CONTAINER" >&2; exit 1; }
node --test keycloak.mjs
