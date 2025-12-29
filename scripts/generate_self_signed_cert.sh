#!/usr/bin/env bash
set -euo pipefail

DOMAIN=${1:-localhost}
CERT_DIR="$(cd "$(dirname "$0")/../nginx/certs" && pwd)"
mkdir -p "$CERT_DIR"

SAN="DNS:${DOMAIN},DNS:localhost,IP:127.0.0.1"
CMD="apk add --no-cache openssl >/dev/null && openssl req -x509 -nodes -newkey rsa:2048 -days 365 -keyout /certs/privkey.pem -out /certs/fullchain.pem -subj \"/CN=${DOMAIN}\" -addext \"subjectAltName=${SAN}\""

docker run --rm -v "${CERT_DIR}:/certs" alpine:3.19 sh -c "$CMD"
echo "Self-signed cert created in ${CERT_DIR}"
