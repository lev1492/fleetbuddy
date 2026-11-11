#!/usr/bin/env bash
# Certificato autofirmato per servire l'interfaccia di Fabbrix Farm Manager in HTTPS (rete locale).
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"; cd "$DIR/certs"
HOST=$(hostname); IP=$(hostname -I | awk '{print $1}')
openssl req -x509 -newkey rsa:2048 -nodes -days 1825 -keyout ui.key -out ui.crt \
  -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST,DNS:$HOST.local,IP:$IP" 2>/dev/null
chmod 600 ui.key
echo "Creati certs/ui.crt e certs/ui.key. In config.toml, sezione [app]:"
echo '  tls_cert = "certs/ui.crt"'
echo '  tls_key  = "certs/ui.key"'
echo "Poi: sudo systemctl restart fleetbuddy  →  https://$IP:8000 (il browser chiederà di accettare il certificato)"
