#!/usr/bin/env bash
# Backup di ciò che non si può ricreare senza un ripristino di fabbrica del Hub:
#  - certs/   (il Hub accetta SOLO il certificato client usato all'attivazione)
#  - config.toml (password dell'account API: se persa serve il ripristino di fabbrica)
#  - data/fleetbuddy.db (storico, coda, archivio metadati)
# Uso: bash tools/backup.sh [cartella_destinazione]   (es. una chiavetta USB)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"; cd "$DIR"
DEST="${1:-$HOME}"; mkdir -p "$DEST"; OUT="$DEST/fleetbuddy-backup-$(date +%Y%m%d-%H%M).tar.gz"
umask 077
DB=""
if [ -f data/fleetbuddy.db ]; then
  python3 -c "import sqlite3;s=sqlite3.connect('data/fleetbuddy.db');d=sqlite3.connect('data/backup.db');s.backup(d);d.close()"
  DB="data/backup.db"
fi
tar -czf "$OUT" certs config.toml $DB
rm -f data/backup.db
echo "Backup creato: $OUT"
echo "Contiene chiavi private e password: conservalo in un posto sicuro (non su cloud condivisi)."
