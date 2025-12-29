param(
  [Parameter(Mandatory=$true)][string]$Domain,
  [Parameter(Mandatory=$true)][string]$Email
)

$ErrorActionPreference = "Stop"

$baseDir = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$leDir = Join-Path $baseDir "nginx\letsencrypt"
$webrootDir = Join-Path $baseDir "nginx\www"

New-Item -ItemType Directory -Force -Path (Join-Path $leDir "live\$Domain") | Out-Null
New-Item -ItemType Directory -Force -Path $webrootDir | Out-Null

$fullchain = Join-Path $leDir "live\$Domain\fullchain.pem"
if (-Not (Test-Path $fullchain)) {
  Write-Host "Creating dummy cert for $Domain"
  $cmd = @(
    "apk add --no-cache openssl >/dev/null",
    "openssl req -x509 -nodes -newkey rsa:2048 -days 1",
    "-keyout /etc/letsencrypt/live/$Domain/privkey.pem",
    "-out /etc/letsencrypt/live/$Domain/fullchain.pem",
    "-subj \"/CN=$Domain\""
  ) -join " "

  docker run --rm -v "${leDir}:/etc/letsencrypt" alpine:3.19 sh -c $cmd
}

Write-Host "Starting nginx for ACME challenge"
docker compose -f "$baseDir\docker-compose.yml" -f "$baseDir\docker-compose.letsencrypt.yml" up -d nginx

Write-Host "Requesting Let's Encrypt certificate"
docker compose -f "$baseDir\docker-compose.yml" -f "$baseDir\docker-compose.letsencrypt.yml" run --rm certbot certonly `
  --webroot -w /var/www/certbot `
  -d "$Domain" `
  -m "$Email" `
  --agree-tos --no-eff-email --force-renewal

Write-Host "Reloading nginx"
docker compose -f "$baseDir\docker-compose.yml" -f "$baseDir\docker-compose.letsencrypt.yml" exec nginx nginx -s reload

Write-Host "Done. Certificates stored in $leDir\live\$Domain"
