param(
  [string]$Domain = "localhost"
)

$ErrorActionPreference = "Stop"
$certDir = Join-Path $PSScriptRoot "..\nginx\certs"
New-Item -ItemType Directory -Force -Path $certDir | Out-Null

$san = "DNS:$Domain,DNS:localhost,IP:127.0.0.1"
$cmd = @(
  "apk add --no-cache openssl >/dev/null",
  "openssl req -x509 -nodes -newkey rsa:2048 -days 365",
  "-keyout /certs/privkey.pem",
  "-out /certs/fullchain.pem",
  "-subj \"/CN=$Domain\"",
  "-addext \"subjectAltName=$san\""
) -join " "

docker run --rm -v "${certDir}:/certs" alpine:3.19 sh -c $cmd
Write-Host "Self-signed cert created in $certDir"
