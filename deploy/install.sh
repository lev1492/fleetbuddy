#!/usr/bin/env bash
# Installazione su Raspberry Pi OS (Bookworm, 32 o 64 bit). Lanciare dalla cartella del progetto:
#   bash deploy/install.sh
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
echo "==> Pacchetti di sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip openssl git curl ffmpeg
PYV=$(python3 -c 'import sys;print(sys.version_info >= (3,11))')
[ "$PYV" = "True" ] || { echo "Serve Python 3.11+ (Raspberry Pi OS Bookworm)"; exit 1; }
echo "==> Ambiente virtuale"
python3 -m venv .venv
# piwheels fornisce i pacchetti precompilati per ARM: evita compilazioni lunghe sul Pi 3
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q --extra-index-url https://www.piwheels.org/simple -r requirements.txt
[ -f config.toml ] || { cp config.example.toml config.toml; echo "==> Creato config.toml: modificalo prima di avviare"; }
mkdir -p certs data
# credenziali: leggibili solo dall'utente che esegue FleetBuddy
chmod 700 certs data
chmod 600 config.toml
find certs -type f -name "*.key" -exec chmod 600 {} +
echo "==> Servizio systemd"
sed -e "s#__DIR__#$DIR#g" -e "s#__USER__#$USER#g" deploy/fleetbuddy.service | sudo tee /etc/systemd/system/fleetbuddy.service >/dev/null
# aggiornamenti: comando fleetbuddy-update, timer notturno e permesso di riavviare il servizio senza password
sudo rm -f /usr/local/bin/fleetbuddy-update
printf '#!/bin/sh\nexec /bin/bash "%s/tools/update.sh" "$@"\n' "$DIR" | sudo tee /usr/local/bin/fleetbuddy-update >/dev/null
sudo chmod 755 /usr/local/bin/fleetbuddy-update
for unit in fleetbuddy-update.service fleetbuddy-update.timer fleetbuddy-update-now.service; do
  sed -e "s#__DIR__#$DIR#g" -e "s#__USER__#$USER#g" "deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
# riavvio del servizio e aggiornamento avviato dal browser (solo questi due comandi, senza password)
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl restart fleetbuddy\n%s ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block fleetbuddy-update-now.service\n' \
  "$USER" "$USER" | sudo tee /etc/sudoers.d/fleetbuddy >/dev/null
sudo chmod 440 /etc/sudoers.d/fleetbuddy
sudo visudo -cf /etc/sudoers.d/fleetbuddy >/dev/null || { sudo rm -f /etc/sudoers.d/fleetbuddy; echo "Regola sudo non valida, rimossa"; }
sudo systemctl daemon-reload
sudo systemctl enable fleetbuddy
if [ -d .git ]; then sudo systemctl enable --now fleetbuddy-update.timer; fi
echo
echo "Fatto. Prossimi passi:"
echo "  1. copia certificato e chiave client in $DIR/certs/ e la CA del Hub (cert/root-ca.crt della demo)"
echo "  2. modifica $DIR/config.toml"
echo "  2b. trova il Hub:  .venv/bin/python -m tools.find_hub --write"
echo "  3. se il Hub non è ancora attivato:  .venv/bin/python -m tools.activate"
echo "  3b. fai subito un backup di certs/ e config.toml:  bash tools/backup.sh"
echo "  Aggiornamenti futuri: fleetbuddy-update"
echo "  4. sudo systemctl start fleetbuddy  →  http://$(hostname -I | awk '{print $1}'):8000"
