#!/usr/bin/env bash
# Pubblica una nuova versione di Fabbrix Farm Manager (da eseguire sul PC di sviluppo, non sul Pi).
#
#   tools/release.sh 0.2.0
#
# Controlla che il lavoro sia tutto salvato, che CHANGELOG.md abbia la sezione "## [0.2.0]"
# e che le traduzioni siano complete; poi aggiorna la versione, crea il commit e il tag v0.2.0
# e li invia a GitHub. I Raspberry Pi la vedranno al prossimo fleetbuddy-update.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "Serve Python 3 per i controlli."; exit 1; }
NEW="${1:-}"
[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Uso: tools/release.sh X.Y.Z"; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "Ci sono modifiche non salvate (git status): fai prima il commit."; exit 1; }
git rev-parse "v$NEW" >/dev/null 2>&1 && { echo "Il tag v$NEW esiste già."; exit 1; }
grep -q "^## \[$NEW\]" CHANGELOG.md || { echo "Aggiungi in CHANGELOG.md la sezione: ## [$NEW] – $(date +%F)"; exit 1; }
"$PY" -m tools.i18n_check --strict >/dev/null || { echo "Traduzioni incomplete: python3 -m tools.i18n_check"; exit 1; }
"$PY" -m compileall -q app tools >/dev/null
sed -i.bak "s/^__version__ = \".*\"/__version__ = \"$NEW\"/" app/__init__.py && rm -f app/__init__.py.bak
git add app/__init__.py
# gli script devono restare eseguibili anche se il repository viene gestito da Windows
git update-index --chmod=+x tools/*.sh deploy/*.sh
git commit -q -m "Versione $NEW"
git tag -a "v$NEW" -m "Fabbrix Farm Manager $NEW"
git push -q origin HEAD
git push -q origin "v$NEW"
echo "Pubblicata la versione $NEW (tag v$NEW)."
