#!/usr/bin/env bash
set -euo pipefail

DOMAIN=${1:-}
EMAIL=${2:-}

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  echo "Usage: $0 <domain> <email>"
  exit 1
fi

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LE_DIR="$BASE_DIR/nginx/letsencrypt"
WEBROOT_DIR="$BASE_DIR/nginx/www"

mkdir -p "$LE_DIR/live/$DOMAIN" "$WEBROOT_DIR"

if [ ! -e "$LE_DIR/live/$DOMAIN/fullchain.pem" ]; then
  echo "Creating dummy cert for $DOMAIN"
  docker run --rm -v "$LE_DIR:/etc/letsencrypt" alpine:3.19 sh -c \
    "apk add --no-cache openssl >/dev/null && openssl req -x509 -nodes -newkey rsa:2048 -days 1 -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem -subj \"/CN=$DOMAIN\""
fi

echo "Starting nginx for ACME challenge"
docker compose -f "$BASE_DIR/docker-compose.yml" -f "$BASE_DIR/docker-compose.letsencrypt.yml" up -d nginx

echo "Requesting Let's Encrypt certificate"
docker compose -f "$BASE_DIR/docker-compose.yml" -f "$BASE_DIR/docker-compose.letsencrypt.yml" run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" \
  -m "$EMAIL" \
  --agree-tos --no-eff-email --force-renewal

echo "Reloading nginx"
docker compose -f "$BASE_DIR/docker-compose.yml" -f "$BASE_DIR/docker-compose.letsencrypt.yml" exec nginx nginx -s reload

echo "Done. Certificates stored in $LE_DIR/live/$DOMAIN"
