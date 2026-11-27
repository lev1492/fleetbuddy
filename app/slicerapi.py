"""API per i plugin Fabbrix Farm Manager di Cura e OrcaSlicer (plugins/).

Abbinamento senza copiare chiavi:
  1. il plugin chiede un abbinamento:            POST /slicer/v1/pair {app, device}  → {request_id, code, approve_url}
  2. l'utente, già entrato nel Farm Manager, apre approve_url (/#pair=CODICE) e approva: si crea una chiave a suo nome
  3. il plugin interroga                          GET /slicer/v1/pair/{request_id}  → 202 in attesa / 200 {key}

Con la chiave (intestazione X-Api-Key, come per l'API OctoPrint):
  GET  /slicer/v1/info     cartelle, gruppi, stampanti e permessi dell'utente, per la finestra di invio
  POST /slicer/v1/upload   file + cartella, destinazione, azione (upload/order), copie, priorità, note

Il percorso non inizia con /api/, quindi la sessione del browser non serve; le rotte /api/slicer/... invece sono
dell'interfaccia (approvazione dell'abbinamento e download dei plugin) e richiedono la sessione.
"""
from __future__ import annotations

import asyncio
import io
import json
import secrets
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import __version__, ingest
from .i18n import _
from .octoprint import OpError, _deps, _order, _target, authenticate, slicer_allowed

router = APIRouter()
PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
PAIR_TTL = 600                       # secondi di validità di una richiesta di abbinamento
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # niente 0/O, 1/I: si legge e si scrive senza errori
_pairs: dict[str, dict] = {}         # request_id -> richiesta (in memoria: un riavvio annulla le richieste aperte)


def _err(status: int, msg: str, **extra) -> JSONResponse:
    return JSONResponse({"error": msg, "detail": msg, **extra}, status_code=status)


def _clean_pairs():
    now = time.time()
    for rid in [k for k, v in _pairs.items() if v["expires"] < now]:
        _pairs.pop(rid, None)


def _pair_by_code(code: str) -> dict | None:
    _clean_pairs()
    code = "".join(c for c in (code or "").upper() if c.isalnum())
    return next((p for p in _pairs.values() if p["code"] == code and p["status"] == "pending"), None)


# ---------------------------------------------------------------------- abbinamento (plugin)
@router.post("/slicer/v1/pair", include_in_schema=False)
async def pair_request(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    _clean_pairs()
    ip = request.client.host if request.client else "?"
    if sum(1 for p in _pairs.values() if p["ip"] == ip) >= 3 or len(_pairs) >= 50:
        return _err(429, _("Troppe richieste di abbinamento aperte: riprova tra qualche minuto"))
    code = "".join(secrets.choice(CODE_CHARS) for _i in range(6))
    while any(p["code"] == code for p in _pairs.values()):
        code = "".join(secrets.choice(CODE_CHARS) for _i in range(6))
    rid = secrets.token_urlsafe(24)
    _pairs[rid] = {"code": code, "app": " ".join(str(body.get("app") or "Slicer").split())[:40],
                   "device": " ".join(str(body.get("device") or "").split())[:60], "ip": ip, "created": time.time(),
                   "expires": time.time() + PAIR_TTL, "status": "pending", "key": None, "user": None}
    base = str(request.base_url).rstrip("/")
    return {"request_id": rid, "code": code, "expires_in": PAIR_TTL, "approve_url": f"{base}/#pair={code}"}


@router.get("/slicer/v1/pair/{rid}", include_in_schema=False)
async def pair_status(rid: str):
    _clean_pairs()
    p = _pairs.get(rid)
    if not p:
        return _err(404, _("Richiesta di abbinamento scaduta: ricomincia dal plugin"), status="expired")
    if p["status"] == "pending":
        return JSONResponse({"status": "pending", "code": p["code"]}, status_code=202)
    _pairs.pop(rid, None)                      # la chiave si consegna una volta sola
    if p["status"] == "denied":
        return _err(403, _("Abbinamento rifiutato nel Farm Manager"), status="denied")
    return {"status": "approved", "key": p["key"], "user": p["user"]}


# ---------------------------------------------------------------------- abbinamento (interfaccia, con sessione)
def _admin_gate(request: Request) -> JSONResponse | None:
    if not slicer_allowed(request.state.user):
        return _err(403, _("L'invio dagli slicer è per ora riservato agli amministratori"))
    return None


@router.get("/api/slicer/pair/{code}", include_in_schema=False)
async def pair_info(request: Request, code: str):
    if (denied := _admin_gate(request)) is not None:
        return denied
    p = _pair_by_code(code)
    if not p:
        return _err(404, _("Codice non valido o scaduto"))
    return {"code": p["code"], "app": p["app"], "device": p["device"], "ip": p["ip"], "created": p["created"]}


@router.post("/api/slicer/pair/{code}", include_in_schema=False)
async def pair_decide(request: Request, code: str):
    if (denied := _admin_gate(request)) is not None:
        return denied
    u = request.state.user
    p = _pair_by_code(code)
    if not p:
        return _err(404, _("Codice non valido o scaduto"))
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not body.get("approve"):
        p["status"] = "denied"
        return {"ok": True}
    if not u.can("files"):
        return _err(403, _("Il tuo ruolo non consente di caricare file"))
    auth = _deps["auth"]()
    name = f"{p['app']} – {p['device']}" if p["device"] else p["app"]
    _kid, key = auth.create_api_key(u.id, name)
    p.update(status="approved", key=key, user=u.username)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------------- download dei plugin (con sessione)
def cura_package() -> bytes:
    """Pacchetto .curapackage (zip): package.json + files/plugins/FabbrixFarmManager/..."""
    src = PLUGINS_DIR / "cura" / "FabbrixFarmManager"
    meta = json.loads((src / "plugin.json").read_text(encoding="utf-8"))
    pkg = {"package_id": "FabbrixFarmManager", "package_type": "plugin", "display_name": meta["name"],
           "description": meta["description"], "package_version": meta["version"], "sdk_version_semver": "8.0.0",
           "website": "", "author": {"author_id": "Fabbrix", "display_name": meta["author"], "website": ""},
           "tags": ["printing", "output device"]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("package.json", json.dumps(pkg, indent=2))
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and not f.name.endswith(":Zone.Identifier"):
                z.write(f, "files/plugins/FabbrixFarmManager/" + f.relative_to(src).as_posix())
    return buf.getvalue()


@router.get("/api/slicer/plugin/{name}", include_in_schema=False)
async def plugin_download(request: Request, name: str):
    if (denied := _admin_gate(request)) is not None:
        return denied
    if name == "FabbrixFarmManager.curapackage":
        data = await asyncio.to_thread(cura_package)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="FabbrixFarmManager.curapackage"'})
    if name == "fabbrix_farm_manager.py":
        data = (PLUGINS_DIR / "orca" / "fabbrix_farm_manager.py").read_bytes()
        return Response(data, media_type="text/x-python",
                        headers={"Content-Disposition": 'attachment; filename="fabbrix_farm_manager.py"'})
    return _err(404, "Not found")


# ---------------------------------------------------------------------- informazioni per la finestra di invio
def _project_paths(db) -> dict[int, str]:
    rows = {r["id"]: r for r in db.q("SELECT id, name, parent_id FROM projects")}
    out = {}
    for pid in rows:
        parts, cur, seen = [], rows.get(pid), set()
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            parts.append(cur["name"])
            cur = rows.get(cur["parent_id"])
        out[pid] = " / ".join(reversed(parts))
    return out


@router.get("/slicer/v1/info", include_in_schema=False)
async def info(request: Request):
    try:
        u = await authenticate(request)
    except OpError as e:
        return _err(e.status, e.msg)
    svc = _deps["svc"]()
    db = svc.db
    paths = _project_paths(db)
    projects = sorted(({"id": pid, "path": path} for pid, path in paths.items() if u.sees_project(pid)),
                      key=lambda x: x["path"].lower())
    if u.sees_all:
        projects.insert(0, {"id": None, "path": _("Senza progetto")})
    me = db.one("SELECT default_project FROM users WHERE id=?", (u.id,)) or {}
    members: dict[int, list[str]] = {}
    for r in db.q("SELECT group_id, printer_sn FROM group_printers"):
        members.setdefault(r["group_id"], []).append(r["printer_sn"])
    groups = [{"id": g["id"], "name": g["name"], "printers": [sn for sn in members.get(g["id"], []) if sn in svc.printers]}
              for g in db.q("SELECT id, name, owner_id FROM printer_groups ORDER BY name")
              if g["owner_id"] in (None, u.id) and _deps["group_targetable"](u, g["id"])]
    printers = sorted(({"sn": p["sn"], "name": p["name"], "model": p.get("model") or "", "online": bool(p.get("online")),
                        "state": p.get("state") or "", "kind": "bambu" if p["sn"] not in svc.direct else svc.direct[p["sn"]].kind}
                       for p in svc.printers.values() if u.sees_printer(p["sn"])), key=lambda x: x["name"].lower())
    return {"server": "Fabbrix Farm Manager", "version": __version__, "user": u.username,
            "perms": {"upload": u.can("files"), "order": u.can("production"),
                      "prioritize": bool(_deps.get("can_prioritize", lambda _u: False)(u))},
            "default_project": me.get("default_project"), "projects": projects, "groups": groups, "printers": printers,
            "orders_paused": bool((_deps["settings"]() or {}).get("slicer_orders_paused"))}


# ---------------------------------------------------------------------- invio
def _project_by_id(u, raw: str) -> int | None:
    db = _deps["svc"]().db
    raw = str(raw or "").strip()
    if raw in ("", "default"):
        pid = (db.one("SELECT default_project FROM users WHERE id=?", (u.id,)) or {}).get("default_project")
        if pid is not None and db.one("SELECT 1 FROM projects WHERE id=?", (pid,)) and u.sees_project(pid):
            return pid
        if u.sees_all:
            return None
        raise OpError(400, _("Scegli una cartella dei Files"))
    if raw == "none":
        if not u.sees_all:
            raise OpError(403, _("Scegli un progetto a cui hai accesso"))
        return None
    if not raw.isdigit() or not db.one("SELECT 1 FROM projects WHERE id=?", (int(raw),)):
        raise OpError(400, _("Cartella non trovata"))
    if not u.sees_project(int(raw)):
        raise OpError(403, _("Scegli un progetto a cui hai accesso"))
    return int(raw)


def _parse_target(raw: str) -> tuple[str | None, str | None]:
    raw = str(raw or "").strip()
    if raw[:2] in ("g:", "p:") and len(raw) > 2:
        return raw[0], raw[2:]
    return None, None


@router.post("/slicer/v1/upload", include_in_schema=False)
async def upload(request: Request):
    try:
        u = await authenticate(request)
        form = await request.form()
        up = form.get("file")
        if up is None or not hasattr(up, "filename"):
            raise OpError(400, _("Nessun file ricevuto"))
        try:
            if not u.can("files"):
                raise OpError(403, _("Il tuo ruolo non consente di caricare file"))
            order = str(form.get("action") or "upload") == "order"
            if order and not u.can("production"):
                raise OpError(403, _("Il tuo ruolo non consente di creare ordini di produzione: usa \"Carica\""))
            pid = _project_by_id(u, str(form.get("project_id") or ""))
            kind, ref = _parse_target(str(form.get("target") or ""))
            target = _target(u, kind, ref)
            name = Path(str(up.filename or "").replace("\\", "/")).name
            if not name:
                raise OpError(400, _("Nessun file ricevuto"))
            source = ingest.source_from_agent(request.headers.get("user-agent"))
            svc = _deps["svc"]()
            try:
                r = await asyncio.to_thread(ingest.ingest, svc, up.file, name, pid, u.id, source)
            except ValueError as e:
                raise OpError(400, str(e)) from e
        finally:
            if up is not None and hasattr(up, "close"):
                await up.close()
        notes = str(form.get("notes") or "").strip()[:2000]
        if notes:
            row = svc.db.one("SELECT notes FROM files WHERE id=?", (r["id"],)) or {}
            if not r["duplicate"] or not (row.get("notes") or "").strip():
                svc.db.x("UPDATE files SET notes=? WHERE id=?", (notes, r["id"]))
        out = {"file_id": r["id"], "filename": r["filename"], "duplicate": r["duplicate"], "renamed": r["renamed"],
               "converted": r["converted"], "production_id": None,
               "url": str(request.base_url).rstrip("/") + "/#archive"}
        if order:
            try:
                copies = int(str(form.get("copies") or "1"))
                priority = int(str(form.get("priority") or "1"))
            except ValueError:
                copies, priority = 1, 1
            try:
                out["production_id"] = _order(u, r["id"], target, ingest.source_label(source) or "Slicer", copies, priority)
            except OpError as e:            # il file è comunque nei Files: lo si dice al plugin
                return _err(e.status, e.msg, file_id=r["id"], filename=r["filename"])
            out["url"] = str(request.base_url).rstrip("/") + "/#production"
        return out
    except OpError as e:
        return _err(e.status, e.msg)
