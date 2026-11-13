#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
: "${REVIEWER_TEST_DATABASE_URL:?Set this to a disposable PostgreSQL 16 test database}"
export REVIEWER_API_DIR="${REVIEWER_API_DIR:-$(cd ../../ui/convergence-api && pwd)}"
test -f "$REVIEWER_API_DIR/reviewer-service.js" || { echo "Set REVIEWER_API_DIR to the checkout containing the reviewer service." >&2; exit 1; }
REVIEWER_FIXTURE_DIR=$(mktemp -d)
export REVIEWER_FIXTURE_DIR
trap 'rm -rf -- "$REVIEWER_FIXTURE_DIR"' EXIT
openssl_req() {
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$1" \
    -keyout "$REVIEWER_FIXTURE_DIR/$1.key" -out "$REVIEWER_FIXTURE_DIR/$1.csr" 2>/dev/null
}
openssl_req root
openssl x509 -req -days 1 -in "$REVIEWER_FIXTURE_DIR/root.csr" -signkey "$REVIEWER_FIXTURE_DIR/root.key" \
  -set_serial 1 -out "$REVIEWER_FIXTURE_DIR/root.pem" -extfile <(echo 'basicConstraints=critical,CA:TRUE') 2>/dev/null
serial=1
for identity in reviewer.test hostile.test localhost installation; do
  openssl_req "$identity"
  serial=$((serial + 1))
  usage=serverAuth
  if [[ "$identity" == installation ]]; then usage=clientAuth; fi
  openssl x509 -req -days 1 -in "$REVIEWER_FIXTURE_DIR/$identity.csr" \
    -CA "$REVIEWER_FIXTURE_DIR/root.pem" -CAkey "$REVIEWER_FIXTURE_DIR/root.key" -set_serial "$serial" \
    -out "$REVIEWER_FIXTURE_DIR/$identity.pem" -extfile <(printf 'subjectAltName=DNS:%s\nextendedKeyUsage=%s\nbasicConstraints=CA:FALSE\n' "$identity" "$usage") 2>/dev/null
done
nss="$REVIEWER_FIXTURE_DIR/browser-home/.pki/nssdb"
mkdir -p "$nss" "$REVIEWER_FIXTURE_DIR/browser-home/.local/share"
"${CERTUTIL:-certutil}" -N -d "sql:$nss" --empty-password
"${CERTUTIL:-certutil}" -A -d "sql:$nss" -n reviewer-fixture -t 'C,,' -i "$REVIEWER_FIXTURE_DIR/root.pem"
ln -s "$REVIEWER_FIXTURE_DIR/browser-home/.pki" "$REVIEWER_FIXTURE_DIR/browser-home/.local/share/pki"
node --test browser.mjs
