"""API compatibile OctoPrint per ricevere i file dagli slicer (PrusaSlicer, OrcaSlicer, Cura, ideaMaker).

Lo slicer vede il Farm Manager come una stampante OctoPrint. L'indirizzo sceglie la destinazione di "Carica e stampa":

  /octoprint/                 tutte le stampanti visibili all'utente (quelle compatibili con il file)
  /octoprint/g/<gruppo>/      le stampanti di un gruppo (id o nome)
  /octoprint/p/<seriale>/     una sola stampante

Lo slicer aggiunge da sé /api/version, /api/files/local, ... L'accesso è con la chiave API personale dell'utente
(intestazione X-Api-Key), che eredita ruolo e permessi. Il campo "path" del caricamento indica la cartella
(progetto/sottocartella) dell'archivio; senza percorso si usa la cartella predefinita dell'utente.
"Carica" archivia il file; "Carica e stampa" crea anche un ordine di produzione (una copia per piatto).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import ingest, production
from .auth import User
from .i18n import _, current_lang

log = logging.getLogger("fleetbuddy.octoprint")
router = APIRouter()
_deps: dict = {}

OCTOPRINT_VERSION = "1.10.2"


def init(**deps):
    """Collega il modulo al resto dell'applicazione: svc(), auth(), group_targetable(user, gid), settings()."""
    _deps.update(deps)


class OpError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _api_key(request: Request) -> str:
    key = request.headers.get("x-api-key") or request.query_params.get("apikey") or ""
    if not key:
        a = request.headers.get("authorization") or ""
        if a.lower().startswith("bearer "):
            key = a[7:]
    return key.strip()


# ---------------------------------------------------------------------- destinazione e cartella
def _target(u: User, kind: str | None, ref: str | None) -> tuple[list[str], str]:
    """Stampanti della destinazione visibili all'utente e nome da mostrare."""
    svc = _deps["svc"]()
    if kind is None:
        return [sn for sn in svc.printers if u.sees_printer(sn)], _("tutte le stampanti")
    if kind == "p":
        p = svc.printers.get(ref)
        if not p or not u.sees_printer(ref):
            raise OpError(404, _("Stampante {p} non trovata", p=ref))
        return [ref], p["name"]
    rows = svc.db.q("SELECT id, name, owner_id FROM printer_groups WHERE " + ("id=?" if ref.isdigit() else "name=? COLLATE NOCASE"),
                    (int(ref) if ref.isdigit() else ref,))
    g = next((r for r in rows if r["owner_id"] in (None, u.id)), None)
    if not g or not _deps["group_targetable"](u, g["id"]):
        raise OpError(404, _("Gruppo {g} non trovato", g=ref))
    members = {r["printer_sn"] for r in svc.db.q("SELECT printer_sn FROM group_printers WHERE group_id=?", (g["id"],))}
    return [sn for sn in svc.printers if sn in members and u.sees_printer(sn)], g["name"]


def _project(u: User, folder: str) -> int | None:
    """Cartella dell'archivio indicata dal percorso del caricamento (es. "Clienti/Rossi")."""
    db = _deps["svc"]().db
    parts = [p.strip() for p in re.split(r"[\\/]+", folder or "") if p.strip() and p.strip() != "."]
    if not parts:
        row = db.one("SELECT default_project FROM users WHERE id=?", (u.id,)) or {}
        pid = row.get("default_project")
        if pid is not None and db.one("SELECT 1 FROM projects WHERE id=?", (pid,)) and u.sees_project(pid):
            return pid
        if u.sees_all:
            return None
        raise OpError(400, _("Indica la cartella nel nome del file (es. Progetto/pezzo.gcode) oppure scegli una "
                             "cartella predefinita in Fabbrix Farm Manager → Collega slicer"))
    pid = None
    for i, name in enumerate(parts):
        row = db.one("SELECT id FROM projects WHERE name=? COLLATE NOCASE AND parent_id IS ?", (name, pid))
        if not row:
            raise OpError(400, _("La cartella {path} non esiste nei Files: creala prima nel Farm Manager",
                                 path="/".join(parts[:i + 1])))
        pid = row["id"]
    if not u.sees_project(pid):
        raise OpError(403, _("Non hai accesso alla cartella {path}", path="/".join(parts)))
    return pid


# ---------------------------------------------------------------------- risposte statiche
def _version() -> dict:
    return {"api": "0.1", "server": OCTOPRINT_VERSION, "text": f"OctoPrint {OCTOPRINT_VERSION}"}


def _printer_state() -> dict:
    flags = {"operational": True, "ready": True, "printing": False, "paused": False, "pausing": False,
             "cancelling": False, "error": False, "closedOrError": False, "sdReady": False, "finishing": False,
             "resuming": False}
    return {"text": "Operational", "flags": flags}


def _static(sub: str, u: User, label: str) -> dict | None:
    if sub == "api/version":
        return _version()
    if sub == "api/server":
        return {"version": OCTOPRINT_VERSION, "safemode": None}
    if sub == "api/connection":
        return {"current": {"state": "Operational", "port": "FABBRIX", "baudrate": 115200, "printerProfile": "_default"},
                "options": {"ports": ["FABBRIX"], "baudrates": [115200], "printerProfiles": [{"id": "_default", "name": label}],
                            "portPreference": "FABBRIX", "baudratePreference": 115200, "printerProfilePreference": "_default",
                            "autoconnect": True}}
    if sub == "api/printer":
        return {"state": _printer_state(), "temperature": {}, "sd": {"ready": False}}
    if sub == "api/job":
        return {"job": {"file": {"name": None, "origin": None, "size": None, "date": None}, "estimatedPrintTime": None,
                        "filament": None, "user": None},
                "progress": {"completion": None, "filepos": None, "printTime": None, "printTimeLeft": None},
                "state": "Operational"}
    if sub == "api/settings":
        return {"api": {"allowCrossOrigin": False}, "feature": {"sdSupport": False},
                "webcam": {"webcamEnabled": False, "streamUrl": "", "snapshotUrl": ""}, "plugins": {}}
    if sub == "api/printerprofiles":
        prof = {"id": "_default", "name": label, "model": "Fabbrix Farm Manager", "default": True, "current": True,
                "heatedBed": True, "heatedChamber": False, "color": "default",
                "volume": {"width": 250, "depth": 250, "height": 250, "formFactor": "rectangular", "origin": "lowerleft"},
                "extruder": {"count": 1, "nozzleDiameter": 0.4, "sharedNozzle": False}}
        return {"profiles": {"_default": prof}}
    if sub in ("api/currentuser", "api/login"):
        return {"name": u.username, "active": True, "user": True, "admin": u.is_admin, "apikey": None, "groups": ["users"],
                "permissions": [], "needs": {"role": ["user"], "group": ["users"]}}
    if sub in ("api/files", "api/files/local"):
        # gli slicer usano l'elenco solo per avvisare dei doppioni: qui i nomi uguali non si sovrascrivono mai
        return {"files": [], "free": 1 << 40, "total": 1 << 40}
    return None


# ---------------------------------------------------------------------- caricamento
async def _upload(request: Request, u: User, target: tuple[list[str], str]) -> Response:
    svc = _deps["svc"]()
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "filename"):
        raise OpError(400, _("Nessun file ricevuto"))
    try:
        if not u.can("files"):
            raise OpError(403, _("Il tuo ruolo non consente di caricare file"))
        want_print = str(form.get("print") or "").lower() in ("true", "1", "yes", "on")
        if want_print and not u.can("production"):
            raise OpError(403, _("Il tuo ruolo non consente di creare ordini di produzione: usa \"Carica\""))
        full = PurePosixPath(str(form.get("path") or "").replace("\\", "/")) / str(up.filename or "").replace("\\", "/")
        name = full.name
        if not name or name in (".", ".."):
            raise OpError(400, _("Nessun file ricevuto"))
        pid = _project(u, str(full.parent))
        source = ingest.source_from_agent(request.headers.get("user-agent"))
        try:
            r = await asyncio.to_thread(ingest.ingest, svc, up.file, name, pid, u.id, source)
        except ValueError as e:
            raise OpError(400, str(e)) from e
    finally:
        await up.close()
    slicer = ingest.source_label(source) or "Slicer"
    log.info("%s: file %s da %s (%s)%s", u.username, r["filename"], slicer, "doppione" if r["duplicate"] else "nuovo",
             " → ordine" if want_print else "")
    order = None
    if want_print:
        order = _order(u, r["id"], target, slicer)
    loc = str(request.base_url).rstrip("/") + "/octoprint/api/files/local/" + quote(r["filename"])
    body = {"done": True, "effectiveSelect": False, "effectivePrint": bool(order),
            "files": {"local": {"name": r["filename"], "display": r["filename"], "path": r["filename"], "origin": "local",
                                "refs": {"resource": loc, "download": loc}}},
            "fabbrix": {"file_id": r["id"], "duplicate": r["duplicate"], "production_id": order}}
    return JSONResponse(body, status_code=201, headers={"Location": loc})


def _order(u: User, fid: int, target: tuple[list[str], str], slicer: str, copies: int = 1, priority: int = 1) -> int:
    """Ordine di produzione (copies copie di ogni piatto) sulle stampanti compatibili della destinazione."""
    svc = _deps["svc"]()
    f = svc.db.file(fid)
    printers, label = target
    ok, reasons = [], []
    for sn in printers:
        good, why = production.check(svc, f, sn)
        (ok if good else reasons).append(sn if good else why)
    if not ok:
        why = next((r for r in reasons if r), "")
        why = _reason(why) if why else _("nessuna stampante disponibile")
        raise OpError(409, _("Il file è nei Files, ma nessuna stampante di {target} è compatibile ({why}): "
                             "ordine non creato", target=label, why=why))
    plates = [int(p.get("index") or 1) for p in f["meta"].get("plates") or []] or [1]
    paused = bool((_deps["settings"]() or {}).get("slicer_orders_paused"))
    name = re.sub(r"(\.gcode)?\.(3mf|gcode|gco|g|bgcode|ufp|ctb|goo|cbddlp)$", "", f["filename"], flags=re.I)
    copies = max(1, min(999, int(copies or 1)))
    priority = max(0, min(3, int(priority))) if _deps.get("can_prioritize", lambda _u: False)(u) else 1
    pid = production.create(svc.db, {"name": name, "priority": priority, "printers": ok, "paused": paused,
                                     "items": [{"file_id": fid, "plate": pl, "copies": copies} for pl in plates]}, u.id)
    svc.toast("{slicer}: nuovo ordine di produzione «{file}» su {target}", "ok", owner=u.id,
              slicer=slicer, file=name, target=label)
    return pid


def _reason(why: str) -> str:
    """Motivo di incompatibilità di production.check (frase del catalogo o {"msg", "params"})."""
    try:
        d = json.loads(why)
        return _(d["msg"], **(d.get("params") or {}))
    except (ValueError, TypeError, KeyError):
        return _(why)


# ---------------------------------------------------------------------- instradamento
def slicer_allowed(u: User) -> bool:
    """Funzioni per gli slicer riservate agli amministratori finché l'impostazione "slicer_admin_only" è attiva."""
    return u.is_admin or not bool((_deps["settings"]() or {}).get("slicer_admin_only", True))


async def authenticate(request: Request) -> User:
    """Utente della chiave API della richiesta (OpError 403/429 altrimenti); imposta la sua lingua."""
    auth = _deps["auth"]()
    ip = request.client.host if request.client else "?"
    if auth.login_blocked(ip):
        raise OpError(429, _("Troppi tentativi errati: riprova tra 5 minuti"))
    key = _api_key(request)
    uid = await asyncio.to_thread(auth.api_key_user_id, key, ip, request.headers.get("user-agent") or "")
    u = auth.load(uid) if uid else None
    if not u:
        if key:
            auth.api_key_failed(ip)
        raise OpError(403, _("Chiave API mancante o non valida: creala in Fabbrix Farm Manager → Collega slicer"))
    if u.language:
        current_lang.set(u.language)
    if not slicer_allowed(u):
        raise OpError(403, _("L'invio dagli slicer è per ora riservato agli amministratori"))
    return u


@router.api_route("/octoprint/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                  include_in_schema=False)
@router.api_route("/octoprint", methods=["GET", "HEAD"], include_in_schema=False)
async def octoprint(request: Request, rest: str = ""):
    parts = [unquote(p) for p in rest.split("/") if p]
    kind = ref = None
    if len(parts) >= 2 and parts[0] in ("g", "p"):
        kind, ref, parts = parts[0], parts[1], parts[2:]
    sub = "/".join(parts)
    if sub in ("", "api"):
        return _err(404, "Not found")
    try:
        u = await authenticate(request)
        target = _target(u, kind, ref)
        if sub == "plugin/appkeys/probe":
            return _err(404, "Not found")                # niente richiesta automatica della chiave: va incollata
        if sub in ("api/files/local", "api/files") and request.method == "POST":
            return await _upload(request, u, target)
        if sub.startswith("api/files/"):
            return _err(404, "Not found")                # file singolo: mai presente (i nomi uguali non si sovrascrivono)
        if request.method in ("GET", "HEAD") or sub == "api/login":
            data = _static(sub, u, target[1])
            if data is not None:
                return JSONResponse(data)
        if sub in ("api/job", "api/printer/command", "api/connection") and request.method == "POST":
            return Response(status_code=204)             # comandi alla "stampante": ignorati (è una coda)
        return _err(404, "Not found")
    except OpError as e:
        return _err(e.status, e.msg)
