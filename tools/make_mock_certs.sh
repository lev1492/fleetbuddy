#!/usr/bin/env bash
# Certificati di prova per tools/mock_hub.py (NON servono con un Hub reale)
set -e
D="$(dirname "$0")/../certs/mock"; mkdir -p "$D"; cd "$D"
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 3650 -subj "/CN=Mock Hub CA" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=fleet_v1.bambulab.com" 2>/dev/null
printf "subjectAltName=DNS:fleet_v1.bambulab.com\n" > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 3650 -extfile san.ext 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout client.key -out client.csr -subj "/CN=fleetbuddy" 2>/dev/null
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt -days 3650 2>/dev/null
rm -f *.csr san.ext
echo "Certificati creati in $D"
