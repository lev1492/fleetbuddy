#!/usr/bin/env bash
# Aggiornamento di FleetBuddy dal repository GitHub (come "apt upgrade", ma per FleetBuddy).
#
#   fleetbuddy-update            controlla e, se c'è una nuova versione, chiede conferma e la installa
#   fleetbuddy-update --check    controlla soltanto (esce con 10 se c'è un aggiornamento)
#   fleetbuddy-update --yes      installa senza chiedere conferma
#   fleetbuddy-update --auto     per il timer notturno: installa solo se [update] auto_install = true
#   fleetbuddy-update --main     usa l'ultima versione di sviluppo (ramo main) invece dell'ultima release
#
# Le release sono i tag vX.Y.Z del repository. Configurazione, certificati, database e
# archivio non sono nel repository, quindi l'aggiornamento non li tocca. Prima di installare
# viene fatto un backup; se dopo il riavvio FleetBuddy non risponde, si torna alla versione precedente.
set -euo pipefail

# Lo script si aggiorna da solo durante l'installazione: lo eseguiamo da una copia temporanea.
# FB_UPDATE_SELF identifica quella copia; solo lei viene cancellata alla fine (mai l'originale).
SELF="$(readlink -f "$0")"
if [ "${FB_UPDATE_SELF:-}" != "$SELF" ]; then
  DIR="$(cd "$(dirname "$SELF")/.." && pwd)"
  tmp="$(mktemp /tmp/fleetbuddy-update.XXXXXX)"
  cp "$SELF" "$tmp"
  FB_UPDATE_DIR="$DIR" FB_UPDATE_SELF="$tmp" exec bash "$tmp" "$@"
fi
DIR="$FB_UPDATE_DIR"
case "$SELF" in /tmp/fleetbuddy-update.*) trap 'rm -f "$SELF"' EXIT ;; esac
# i processi avviati da qui (es. il riavvio del servizio) non devono ereditare queste variabili
unset FB_UPDATE_SELF FB_UPDATE_DIR
cd "$DIR"
MODE=install; CHANNEL=release; YES=0
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;;
    --auto) MODE=auto ;;
    --yes|-y) YES=1 ;;
    --main) CHANNEL=main ;;
    -h|--help) sed -n '2,13p' "$DIR/tools/update.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opzione sconosciuta: $a"; exit 2 ;;
  esac
done

say() { echo "==> $*"; }
die() { echo "ERRORE: $*" >&2; exit 1; }
PY="$DIR/.venv/bin/python"; [ -x "$PY" ] || PY=python3
STATUS="$DIR/data/update.json"
mkdir -p "$DIR/data"

[ -d .git ] || die "$DIR non è un repository git. Vedi \"Passare all'installazione da GitHub\" nel README."

version_of() { sed -n 's/^__version__ = "\(.*\)"/\1/p'; }
current="$(version_of < app/__init__.py)"
git fetch --quiet --tags --prune origin 2>/dev/null || die "impossibile contattare GitHub (rete o chiave di accesso)"

if [ "$CHANNEL" = release ]; then
  target="$(git tag -l 'v[0-9]*' --sort=-v:refname | head -n1)"
  [ -n "$target" ] || die "nel repository non c'è ancora nessuna release (tag vX.Y.Z)"
  latest="${target#v}"
  newest="$(printf '%s\n%s\n' "$current" "$latest" | sort -V | tail -n1)"
  if [ "$latest" != "$current" ] && [ "$newest" = "$latest" ]; then available=true; else available=false; fi
else
  target="origin/main"
  latest="$(git show origin/main:app/__init__.py | version_of)"
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then available=true; else available=false; fi
fi

printf '{"current": "%s", "latest": "%s", "available": %s, "channel": "%s", "checked_at": %s}\n' \
  "$current" "$latest" "$available" "$CHANNEL" "$(date +%s)" > "$STATUS"

if [ "$available" = false ]; then
  say "FleetBuddy è aggiornato (versione $current)."
  exit 0
fi
say "Disponibile la versione $latest (installata: $current)."
say "Ultima versione pubblicata su GitHub: $target"
[ "$MODE" = check ] && exit 10

if [ "$MODE" = auto ]; then
  auto="$("$PY" - <<'PYEOF'
import tomllib, pathlib
p = pathlib.Path("config.toml")
cfg = tomllib.loads(p.read_text()) if p.exists() else {}
print("1" if cfg.get("update", {}).get("auto_install") else "0")
PYEOF
)"
  if [ "$auto" != 1 ]; then say "Installazione automatica disattivata: esegui fleetbuddy-update per installarla."; exit 10; fi
  # una versione che non è partita non viene ritentata ogni notte: solo a mano
  if [ -f "$DIR/data/update-failed" ] && [ "$(cat "$DIR/data/update-failed")" = "$latest" ]; then
    say "La versione $latest non era partita: non la reinstallo in automatico. Usa fleetbuddy-update per riprovare."; exit 10
  fi
  YES=1
fi

# modifiche fatte a mano ai file del programma: meglio fermarsi che perderle
if [ -n "$(git -c core.fileMode=false status --porcelain --untracked-files=no)" ]; then
  git -c core.fileMode=false status --short --untracked-files=no
  die "ci sono file del programma modificati a mano (elencati sopra). Annullali con: git checkout -- .  e riprova."
fi

if [ "$YES" != 1 ]; then
  echo
  echo "Novità (da CHANGELOG.md):"
  git show "$target:CHANGELOG.md" 2>/dev/null | awk '/^## \[/{n++} n==1' | head -n 40 || true
  echo
  read -r -p "Installare la versione $latest? [s/N] " ans
  case "$ans" in s|S|si|SI|y|Y|yes) ;; *) say "Annullato."; exit 0 ;; esac
fi

if [ "$CHANNEL" = release ]; then
  inside="$(git show "$target:app/__init__.py" | version_of)"
  if [ "$inside" != "$latest" ]; then
    die "il tag $target contiene la versione $inside invece di $latest: aggiorna __version__ in app/__init__.py, crea un nuovo tag e riprova"
  fi
fi

prev="$(git rev-parse HEAD)"
say "Backup di configurazione, certificati e database…"
mkdir -p "$HOME/fleetbuddy-backups"
bash tools/backup.sh "$HOME/fleetbuddy-backups" >/dev/null
ls -1t "$HOME"/fleetbuddy-backups/fleetbuddy-backup-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f   # tiene gli ultimi 10

install_rev() {
  git -c advice.detachedHead=false checkout -q "$1"
  "$DIR/.venv/bin/pip" install -q --extra-index-url https://www.piwheels.org/simple -r requirements.txt
  sudo -n systemctl restart fleetbuddy
}

health_url="$("$PY" - <<'PYEOF'
import tomllib, pathlib
p = pathlib.Path("config.toml")
app = (tomllib.loads(p.read_text()) if p.exists() else {}).get("app", {})
scheme = "https" if app.get("tls_cert") and app.get("tls_key") else "http"
print(f"{scheme}://127.0.0.1:{app.get('port', 8000)}/api/session")
PYEOF
)"
healthy() {
  for _ in $(seq 1 30); do
    sleep 2
    v="$(curl -sk --max-time 3 "$health_url" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["app"]["version"])' 2>/dev/null || true)"
    [ "$v" = "$1" ] && return 0
  done
  return 1
}

say "Installazione della versione $latest…"
if install_rev "$target" && healthy "$latest"; then
  printf '{"current": "%s", "latest": "%s", "available": false, "channel": "%s", "checked_at": %s}\n' \
    "$latest" "$latest" "$CHANNEL" "$(date +%s)" > "$STATUS"
  rm -f "$DIR/data/update-failed"
  say "FleetBuddy aggiornato alla versione $latest."
else
  echo "La nuova versione non risponde: ripristino della versione $current…" >&2
  echo "$latest" > "$DIR/data/update-failed"
  install_rev "$prev" || true
  healthy "$current" && echo "Versione $current ripristinata. Controlla il log: journalctl -u fleetbuddy -n 100" >&2
  exit 1
fi
