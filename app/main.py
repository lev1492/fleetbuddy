"""FleetBuddy – interfaccia web e API REST con utenti, gruppi, progetti e permessi."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, REPO_URL, __version__
from .auth import ROLES, Auth, User, password_problem
from .config import BASE_DIR, load_config
from .i18n import LANGS, _, current_lang, from_accept_language, normalize
from .hub_client import HubError
from .printers import FILAMENT_PRESETS, auto_map, firmware_below_min
from .service import Service, qmsg
from .threemf import md5_file, parse_3mf, thumbnail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fleetbuddy.web")
cfg = load_config()
svc: Service | None = None
auth: Auth | None = None
COOKIE = "fb_session"
PUBLIC = {"/", "/api/session", "/api/login", "/api/setup", "/api/logout"}
DEFAULT_SETTINGS = {"camera_refresh_s": 60, "notify_lang": "it"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global svc, auth
    log.info("Avvio %s v%s", APP_NAME, __version__)
    svc = Service(cfg)
    auth = Auth(svc.db)
    if not auth.has_users() and cfg.app.ui_password:
        try:   # primo avvio: l'utente di config.toml diventa amministratore
            auth.create_user(cfg.app.ui_user or "admin", cfg.app.ui_password, "admin")
            log.info("Creato l'amministratore '%s' da config.toml", cfg.app.ui_user)
        except ValueError as e:
            log.warning("Impossibile creare l'amministratore da config.toml (%s): usa la pagina di configurazione", e)
    svc.start()
    yield
    await svc.stop()


app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
APP_INFO = {"name": APP_NAME, "version": __version__, "repo": REPO_URL}


@app.exception_handler(HubError)
async def hub_error_handler(_req, exc: HubError):
    d = exc.to_dict()
    d["detail"] = d["error"] = exc.localized(current_lang.get())
    return JSONResponse(d, status_code=502 if exc.status is None else 400)


@app.exception_handler(ValueError)
async def value_error_handler(_req, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.middleware("http")
async def session_auth(request: Request, call_next):
    path = request.url.path
    # lingua: preferenza dell'utente, poi quella scelta nel browser, poi Accept-Language
    lang = (normalize(request.query_params.get("lang")) or normalize(request.cookies.get("fb_lang"))
            or from_accept_language(request.headers.get("accept-language")))
    current_lang.set(lang)
    if path.startswith("/api/") and path not in PUBLIC:
        uid = auth.session_user_id(request.cookies.get(COOKIE)) if auth else None
        user = auth.load(uid) if uid else None
        if not user:
            return JSONResponse({"detail": _("Accesso richiesto")}, status_code=401)
        request.state.user = user
        if user.language and not request.query_params.get("lang"):
            current_lang.set(user.language)
    return await call_next(request)


# ====================================================================== helper permessi
def S() -> Service:
    assert svc is not None
    return svc


def U(request: Request) -> User:
    return request.state.user


def need_admin(u: User):
    if not u.is_admin:
        raise HTTPException(403, _("Operazione riservata agli amministratori"))


def need_operator(u: User):
    if not u.can_operate:
        raise HTTPException(403, _("Il tuo profilo è di sola visualizzazione"))


def printer_for(u: User, sn: str) -> dict:
    p = S().printers.get(sn)
    if not p or not u.sees_printer(sn):
        raise HTTPException(404, _("Stampante non trovata"))
    return p


def file_for(u: User, fid: int) -> dict:
    f = S().db.file(fid)
    if not f or not u.sees_project(f.get("project_id")):
        raise HTTPException(404, _("File non trovato"))
    return f


def settings() -> dict:
    return {**DEFAULT_SETTINGS, **(S().db.get_setting("ui_settings", {}) or {})}


def groups_with_members() -> list[dict]:
    members = auth.group_members()
    rows = S().db.q("SELECT * FROM printer_groups ORDER BY name")
    for g in rows:
        g["printers"] = sorted(members.get(g["id"], set()))
    return rows


def group_targetable(u: User, gid: int) -> bool:
    if u.is_admin:
        return True
    members = auth.group_members().get(gid, set())
    return bool(members) and members <= u.printers


def queue_visible(u: User, row: dict) -> bool:
    if u.is_admin:
        return True
    if not u.sees_project(row.get("project_id")):
        return False
    if row.get("printer_sn"):
        return u.sees_printer(row["printer_sn"])
    if row.get("group_id"):
        return group_targetable(u, row["group_id"])
    return False


def state_for(u: User) -> dict:
    st = S().snapshot_state()
    st["printers"] = [dict(p) for p in st["printers"] if u.sees_printer(p["sn"])]   # copie: i dati aggiunti sono per utente
    if not u.is_admin:
        rows = S().db.q("SELECT q.printer_sn, q.group_id, f.project_id FROM queue q LEFT JOIN files f "
                        "ON f.id=q.file_id WHERE q.status='queued'")
        st["queue_count"] = sum(1 for r in rows if queue_visible(u, r))
        if not st["hub_ok"]:
            st["hub_error"] = _("Hub non raggiungibile: avvisa l'amministratore")
    elif not st["hub_ok"] and S().hub_error_obj is not None:
        st["hub_error"] = S().hub_error_obj.localized(current_lang.get())
    st["settings"] = settings()
    rows = S().db.q("""SELECT q.id, q.printer_sn, q.group_id, q.copies, f.filename, f.project_id
                       FROM queue q LEFT JOIN files f ON f.id=q.file_id WHERE q.status='queued'
                       ORDER BY q.position, q.id""")
    members = auth.group_members()
    for p in st["printers"]:
        mine = [r for r in rows if queue_visible(u, r) and (r["printer_sn"] == p["sn"] or
                (r["group_id"] and p["sn"] in members.get(r["group_id"], set())) or
                (not r["printer_sn"] and not r["group_id"]))]
        p["next_job"] = {"filename": mine[0]["filename"], "more": len(mine) - 1} if mine else None
    return st


def set_cookie(resp: Response, request: Request, token: str):
    resp.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https")


# ====================================================================== sessione
@app.get("/api/session")
async def session(request: Request):
    uid = auth.session_user_id(request.cookies.get(COOKIE))
    user = auth.load(uid) if uid else None
    out = {"user": user.public() if user else None, "setup_needed": not auth.has_users(), "app": APP_INFO}
    if user and user.is_admin:
        out["update"] = update_status()
    return out


def update_status() -> dict | None:
    """Esito dell'ultimo controllo di tools/update.sh (data/update.json), se presente."""
    try:
        st = json.loads((cfg.data_path / "update.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if st.get("latest") == __version__:      # già installata (anche se aggiornata a mano)
        st["available"] = False
    st["current"] = __version__
    return st


@app.post("/api/setup")
async def setup(request: Request, body: dict):
    if auth.has_users():
        raise HTTPException(409, _("FleetBuddy è già configurato"))
    auth.create_user(body.get("username", ""), body.get("password", ""), "admin",
                     language=normalize(body.get("language")) or current_lang.get())
    return await login(request, body)


@app.post("/api/login")
async def login(request: Request, body: dict):
    ip = request.client.host if request.client else "?"
    if auth.login_blocked(ip):
        raise HTTPException(429, _("Troppi tentativi errati: riprova tra 5 minuti"))
    token = await asyncio.to_thread(auth.login, str(body.get("username", "")), str(body.get("password", "")), ip)
    if not token:
        raise HTTPException(401, _("Nome utente o password errati"))
    resp = JSONResponse({"ok": True})
    set_cookie(resp, request, token)
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    auth.logout(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.patch("/api/me")
async def my_profile(request: Request, body: dict):
    u = U(request)
    if "language" in body:
        lang = normalize(body["language"])
        if not lang:
            raise HTTPException(400, _("Lingua non supportata"))
        S().db.x("UPDATE users SET language=? WHERE id=?", (lang, u.id))
    return {"ok": True}


@app.post("/api/me/password")
async def my_password(request: Request, body: dict):
    u = U(request)
    row = S().db.one("SELECT pw_hash FROM users WHERE id=?", (u.id,))
    from .auth import hash_password, verify_password
    if not verify_password(str(body.get("old", "")), row["pw_hash"]):
        raise HTTPException(400, _("La password attuale non è corretta"))
    problem = password_problem(str(body.get("new", "")))
    if problem:
        raise HTTPException(400, problem)
    S().db.x("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (hash_password(body["new"]), u.id))
    return {"ok": True}


@app.post("/api/me/keep_password")
async def my_keep_password(request: Request):
    """L'utente sceglie di tenere la password ricevuta dall'amministratore."""
    S().db.x("UPDATE users SET must_change_pw=0 WHERE id=?", (U(request).id,))
    return {"ok": True}


# ====================================================================== stato ed eventi
@app.get("/api/state")
async def state(request: Request):
    return state_for(U(request))


@app.get("/api/events")
async def events(request: Request):
    q = S().subscribe()
    user = U(request)

    async def gen():
        nonlocal user
        reload_at = time.time() + 60
        if user.language:
            current_lang.set(user.language)
        try:
            yield f"data: {json.dumps({'kind': 'state', 'data': state_for(user)})}\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if time.time() > reload_at:     # permessi modificati dall'admin: applicati entro un minuto
                    fresh = auth.load(user.id)
                    if not fresh:
                        break
                    user, reload_at = fresh, time.time() + 60
                if user.language:
                    current_lang.set(user.language)
                if msg["kind"] == "state":
                    out = {"kind": "state", "data": state_for(user)}
                elif msg["kind"] == "toast":
                    if msg["data"].get("sn") and not user.sees_printer(msg["data"]["sn"]):
                        continue
                    out = msg
                else:
                    out = msg
                yield f"data: {json.dumps(out)}\n\n"
        finally:
            S().unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/settings")
async def get_settings(request: Request):
    return settings()


@app.put("/api/settings")
async def put_settings(request: Request, body: dict):
    need_admin(U(request))
    cur = settings()
    if "camera_refresh_s" in body:
        cur["camera_refresh_s"] = max(10, min(3600, int(body["camera_refresh_s"])))
    if normalize(body.get("notify_lang")):
        cur["notify_lang"] = normalize(body["notify_lang"])
    S().db.set_setting("ui_settings", cur)
    return cur


# ====================================================================== utenti (admin)
@app.get("/api/users")
async def users_list(request: Request):
    need_admin(U(request))
    rows = S().db.q("SELECT id, username, first_name, last_name, role, active, language, created_at, last_login, "
                    "must_change_pw FROM users ORDER BY username")
    acc = S().db.q("SELECT * FROM access")
    for r in rows:
        mine = [a for a in acc if a["user_id"] == r["id"]]
        r["printers"] = [a["ref"] for a in mine if a["kind"] == "printer"]
        r["groups"] = [int(a["ref"]) for a in mine if a["kind"] == "group"]
        r["projects"] = [int(a["ref"]) for a in mine if a["kind"] == "project"]
    return rows


@app.post("/api/users")
async def users_add(request: Request, body: dict):
    need_admin(U(request))
    uid = await asyncio.to_thread(auth.create_user, str(body.get("username", "")), str(body.get("password", "")),
                                  str(body.get("role", "operator")), normalize(body.get("language")) or current_lang.get(),
                                  str(body.get("first_name", "")), str(body.get("last_name", "")))
    if body.get("must_change", True):
        S().db.x("UPDATE users SET must_change_pw=1 WHERE id=?", (uid,))
    return {"id": uid}


@app.patch("/api/users/{uid}")
async def users_edit(request: Request, uid: int, body: dict):
    me = U(request)
    need_admin(me)
    row = S().db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404)
    demote = ("role" in body and body["role"] != "admin") or ("active" in body and not body["active"])
    if row["role"] == "admin" and demote and auth.admins_left(uid) == 0:
        raise HTTPException(409, _("Deve restare almeno un amministratore attivo"))
    if "role" in body:
        if body["role"] not in ROLES:
            raise HTTPException(400, _("Ruolo non valido"))
        S().db.x("UPDATE users SET role=? WHERE id=?", (body["role"], uid))
    for col in ("first_name", "last_name"):
        if col in body:
            S().db.x(f"UPDATE users SET {col}=? WHERE id=?", (str(body[col]).strip()[:64], uid))
    if normalize(body.get("language")):
        S().db.x("UPDATE users SET language=? WHERE id=?", (normalize(body["language"]), uid))
    if "active" in body:
        S().db.x("UPDATE users SET active=? WHERE id=?", (1 if body["active"] else 0, uid))
        if not body["active"]:
            auth.drop_sessions(uid)
    if body.get("password"):
        problem = password_problem(body["password"])
        if problem:
            raise HTTPException(400, problem)
        from .auth import hash_password
        S().db.x("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(body["password"]), uid))
        if uid != me.id:
            auth.drop_sessions(uid)
            S().db.x("UPDATE users SET must_change_pw=? WHERE id=?", (1 if body.get("must_change", True) else 0, uid))
    return {"ok": True}


@app.delete("/api/users/{uid}")
async def users_delete(request: Request, uid: int):
    me = U(request)
    need_admin(me)
    if uid == me.id:
        raise HTTPException(409, _("Non puoi eliminare il tuo stesso utente"))
    row = S().db.one("SELECT role FROM users WHERE id=?", (uid,))
    if row and row["role"] == "admin" and auth.admins_left(uid) == 0:
        raise HTTPException(409, _("Deve restare almeno un amministratore attivo"))
    S().db.x("DELETE FROM users WHERE id=?", (uid,))
    S().db.x("DELETE FROM access WHERE user_id=?", (uid,))
    auth.drop_sessions(uid)
    return {"ok": True}


@app.put("/api/users/{uid}/access")
async def users_access(request: Request, uid: int, body: dict):
    need_admin(U(request))
    db = S().db
    db.x("DELETE FROM access WHERE user_id=?", (uid,))
    for kind, key in (("printer", "printers"), ("group", "groups"), ("project", "projects")):
        for ref in body.get(key) or []:
            db.x("INSERT OR IGNORE INTO access(user_id, kind, ref) VALUES(?,?,?)", (uid, kind, str(ref)))
    return {"ok": True}


# ====================================================================== gruppi di stampanti
@app.get("/api/groups")
async def groups_list(request: Request):
    u = U(request)
    out = []
    for g in groups_with_members():
        if not u.is_admin:
            g["printers"] = [sn for sn in g["printers"] if u.sees_printer(sn)]
            if not g["printers"]:
                continue
        out.append(g)
    return out


@app.post("/api/groups")
async def groups_add(request: Request, body: dict):
    need_admin(U(request))
    name = str(body.get("name", "")).strip()[:40]
    if not name:
        raise HTTPException(400, _("Dai un nome al gruppo"))
    if S().db.one("SELECT 1 FROM printer_groups WHERE name=?", (name,)):
        raise HTTPException(409, _("Esiste già un gruppo con questo nome"))
    gid = S().db.x("INSERT INTO printer_groups(name, color) VALUES(?,?)", (name, body.get("color") or "#0E7C6B"))
    for sn in body.get("printers") or []:
        S().db.x("INSERT OR IGNORE INTO group_printers VALUES(?,?)", (gid, sn))
    return {"id": gid}


@app.patch("/api/groups/{gid}")
async def groups_edit(request: Request, gid: int, body: dict):
    need_admin(U(request))
    if "name" in body:
        S().db.x("UPDATE printer_groups SET name=? WHERE id=?", (str(body["name"]).strip()[:40], gid))
    if "color" in body:
        S().db.x("UPDATE printer_groups SET color=? WHERE id=?", (body["color"], gid))
    if "printers" in body:
        S().db.x("DELETE FROM group_printers WHERE group_id=?", (gid,))
        for sn in body["printers"]:
            S().db.x("INSERT OR IGNORE INTO group_printers VALUES(?,?)", (gid, sn))
    return {"ok": True}


@app.delete("/api/groups/{gid}")
async def groups_delete(request: Request, gid: int):
    need_admin(U(request))
    db = S().db
    db.x("DELETE FROM printer_groups WHERE id=?", (gid,))
    db.x("DELETE FROM group_printers WHERE group_id=?", (gid,))
    db.x("DELETE FROM access WHERE kind='group' AND ref=?", (str(gid),))
    db.x("UPDATE queue SET status='error', message=? WHERE group_id=? AND status='queued'", (qmsg("gruppo eliminato"), gid))
    return {"ok": True}


# ====================================================================== progetti
@app.get("/api/projects")
async def projects_list(request: Request):
    u = U(request)
    rows = S().db.q("""SELECT p.*, (SELECT COUNT(*) FROM files f WHERE f.project_id=p.id) AS files
                       FROM projects p ORDER BY p.name""")
    rows = [r for r in rows if u.sees_project(r["id"])]
    if u.is_admin:
        n = S().db.one("SELECT COUNT(*) n FROM files WHERE project_id IS NULL")["n"]
        rows.append({"id": None, "name": "Senza progetto", "description": "",
                     "color": "#8A949C", "files": n})
    return rows


@app.post("/api/projects")
async def projects_add(request: Request, body: dict):
    u = U(request)
    need_admin(u)
    name = str(body.get("name", "")).strip()[:60]
    if not name:
        raise HTTPException(400, _("Dai un nome al progetto"))
    if S().db.one("SELECT 1 FROM projects WHERE name=?", (name,)):
        raise HTTPException(409, _("Esiste già un progetto con questo nome"))
    pid = S().db.x("INSERT INTO projects(name, description, color, created_at, created_by) VALUES(?,?,?,?,?)",
                   (name, str(body.get("description", ""))[:500], body.get("color") or "#2B63C6", time.time(), u.id))
    return {"id": pid}


@app.patch("/api/projects/{pid}")
async def projects_edit(request: Request, pid: int, body: dict):
    need_admin(U(request))
    for col, limit in (("name", 60), ("description", 500), ("color", 9)):
        if col in body:
            S().db.x(f"UPDATE projects SET {col}=? WHERE id=?", (str(body[col]).strip()[:limit], pid))
    return {"ok": True}


@app.delete("/api/projects/{pid}")
async def projects_delete(request: Request, pid: int):
    need_admin(U(request))
    db = S().db
    db.x("UPDATE files SET project_id=NULL WHERE project_id=?", (pid,))   # i file restano, senza progetto
    db.x("DELETE FROM projects WHERE id=?", (pid,))
    db.x("DELETE FROM access WHERE kind='project' AND ref=?", (str(pid),))
    return {"ok": True}


# ====================================================================== stampanti
@app.post("/api/printers/{sn}/opt")
async def printer_opt(request: Request, sn: str, body: dict):
    u = U(request)
    need_operator(u)
    p = printer_for(u, sn)
    opt = body.get("opt")
    allowed = {
        "pause": {"RUNNING"}, "resume": {"PAUSE", "PAUSED"}, "stop": {"RUNNING", "PAUSE", "PAUSED", "PREPARE"},
        "bed_clean": {"FINISH", "FAILED"},
    }
    if opt not in allowed:
        raise HTTPException(400, _("Comando non valido"))
    if p["state"] not in allowed[opt]:
        raise HTTPException(409, _("Comando '{opt}' non disponibile nello stato {state}", opt=opt, state=p["state"]))
    if p["mqtt"] != 2:
        raise HTTPException(409, _("Canale MQTT della stampante non connesso, riprova tra poco"))
    res = await asyncio.to_thread(S().hub.opt, sn, opt)
    if opt == "stop":
        S().stopped_by_user.add(sn)
    log.info("%s: comando %s inviato da %s", sn, opt, u.username)
    return res


@app.put("/api/printers/{sn}/name")
async def printer_name(request: Request, sn: str, body: dict):
    u = U(request)
    need_admin(u)
    printer_for(u, sn)
    return await asyncio.to_thread(S().hub.set_name, sn, str(body.get("name", ""))[:64])


@app.get("/api/printers/{sn}/snapshot")
async def printer_snapshot(request: Request, sn: str):
    printer_for(U(request), sn)
    img = await S().get_snapshot(sn, max_age=10)
    if not img:
        raise HTTPException(404, _("Istantanea non disponibile"))
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/printers/{sn}/raw")
async def printer_raw(request: Request, sn: str):
    """Dati grezzi ricevuti dal Hub per una stampante (diagnostica, solo amministratori)."""
    u = U(request)
    need_admin(u)
    printer_for(u, sn)
    return S().raw.get(sn) or {}


@app.get("/api/filament_presets")
async def filament_presets():
    return FILAMENT_PRESETS


@app.post("/api/printers/{sn}/filament")
async def printer_filament(request: Request, sn: str, body: dict):
    u = U(request)
    need_operator(u)
    p = printer_for(u, sn)
    ams_id, slot_id = int(body["ams_id"]), int(body["slot_id"])
    tray = next((t for t in p["trays"] if t["ams_id"] == ams_id and t["slot_id"] == slot_id), None)
    if tray and tray["rfid"]:
        raise HTTPException(409, _("Bobina con tag RFID Bambu: i dati non sono modificabili"))
    if p["ams_busy"]:
        raise HTTPException(409, _("AMS occupato: riprova quando è inattivo"))
    color = str(body.get("tray_color", "FFFFFF")).lstrip("#").upper()
    color = (color + "FF")[:8] if len(color) == 6 else color[:8]
    setting = {
        "tray_type": body["tray_type"], "tray_info_idx": body["tray_info_idx"], "tray_color": color,
        "nozzle_temp_min": int(body.get("nozzle_temp_min", 190)),
        "nozzle_temp_max": int(body.get("nozzle_temp_max", 240)),
        "ams_id": ams_id, "slot_id": slot_id,
    }
    opt = "external_filament_setting" if ams_id in (254, 255) else "ams_filament_setting"
    return await asyncio.to_thread(S().hub.opt, sn, opt, {opt: setting})


@app.get("/api/hms")
async def hms(model: str, code: str, kind: str = "hms"):
    return {"code": code, "description": await S().describe(model, code, kind)}


# ====================================================================== archivio file
@app.get("/api/files")
async def files(request: Request, project: str | None = None):
    u = U(request)
    rows = [f for f in S().db.files() if u.sees_project(f.get("project_id"))]
    if project == "none":
        rows = [f for f in rows if f.get("project_id") is None]
    elif project:
        rows = [f for f in rows if str(f.get("project_id")) == project]
    return rows


@app.post("/api/files")
async def upload_file(request: Request, file: UploadFile = File(...), project_id: str = Form("")):
    u = U(request)
    need_operator(u)
    pid = int(project_id) if project_id.isdigit() else None
    if not u.sees_project(pid):
        raise HTTPException(403, _("Scegli un progetto a cui hai accesso"))
    data = S().cfg.data_path
    tmp = data / "tmp" / f"{uuid.uuid4().hex}.3mf"
    with tmp.open("wb") as out:
        shutil.copyfileobj(file.file, out, 1 << 20)
    try:
        meta = await asyncio.to_thread(parse_3mf, tmp)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, _("File non valido: {e}", e=e))
    md5 = await asyncio.to_thread(md5_file, tmp)
    existing = S().db.one("SELECT id, project_id FROM files WHERE md5=?", (md5,))
    if existing:
        tmp.unlink(missing_ok=True)
        return {"id": existing["id"], "duplicate": True, "project_id": existing["project_id"]}
    rel = f"{cfg.app.data_dir}/archive/{md5}.3mf"
    tmp.rename(cfg.path(rel))
    fid = S().db.x("INSERT INTO files(md5, filename, path, size, uploaded_at, meta, project_id, uploaded_by) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (md5, Path(file.filename or "senza_nome.gcode.3mf").name, rel,
                    cfg.path(rel).stat().st_size, time.time(), json.dumps(meta), pid, u.id))
    return {"id": fid, "duplicate": False}


@app.delete("/api/files/{fid}")
async def delete_file(request: Request, fid: int):
    u = U(request)
    f = file_for(u, fid)
    if not (u.is_admin or (u.can_operate and f.get("uploaded_by") == u.id)):
        raise HTTPException(403, _("Puoi eliminare solo i file caricati da te"))
    cfg.path(f["path"]).unlink(missing_ok=True)
    S().db.x("DELETE FROM files WHERE id=?", (fid,))
    S().db.x("DELETE FROM queue WHERE file_id=? AND status='queued'", (fid,))
    return {"ok": True}


@app.patch("/api/files/{fid}")
async def edit_file(request: Request, fid: int, body: dict):
    u = U(request)
    need_operator(u)
    file_for(u, fid)
    if "notes" in body:
        S().db.x("UPDATE files SET notes=? WHERE id=?", (str(body.get("notes", ""))[:2000], fid))
    if "project_id" in body:
        pid = body["project_id"]
        pid = int(pid) if pid not in (None, "", "none") else None
        if not u.sees_project(pid):
            raise HTTPException(403, _("Non hai accesso al progetto di destinazione"))
        S().db.x("UPDATE files SET project_id=? WHERE id=?", (pid, fid))
    return {"ok": True}


@app.get("/api/files/{fid}/thumb/{plate}")
async def file_thumb(request: Request, fid: int, plate: int):
    f = file_for(U(request), fid)
    img = await asyncio.to_thread(thumbnail, cfg.path(f["path"]), plate)
    if not img:
        raise HTTPException(404)
    return Response(img, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/files/{fid}/download")
async def file_download(request: Request, fid: int):
    f = file_for(U(request), fid)
    return FileResponse(cfg.path(f["path"]), filename=f["filename"])


@app.post("/api/files/{fid}/automap")
async def file_automap(request: Request, fid: int, body: dict):
    u = U(request)
    f = file_for(u, fid)
    p = printer_for(u, body["printer_sn"])
    pl = next((x for x in f["meta"]["plates"] if x["index"] == int(body.get("plate", 1))), None)
    if not pl:
        raise HTTPException(404, _("Piatto non trovato"))
    slots, warnings = auto_map(pl["filaments"], p)
    return {"slots": slots, "warnings": warnings, "filaments": pl["filaments"], "trays": p["trays"]}


@app.post("/api/print")
async def print_now(request: Request, body: dict):
    u = U(request)
    need_operator(u)
    file_for(u, int(body["file_id"]))
    printer_for(u, body["printer_sn"])
    return await S().start_print(int(body["file_id"]), int(body.get("plate", 1)), body["printer_sn"],
                                 body.get("slots"), body.get("options") or {}, user_id=u.id)


# ====================================================================== coda
@app.get("/api/queue")
async def queue_list(request: Request):
    u = U(request)
    rows = S().db.q("""SELECT q.*, f.filename, f.meta, f.project_id, g.name AS group_name, us.username AS created_by_name
                       FROM queue q LEFT JOIN files f ON f.id=q.file_id
                       LEFT JOIN printer_groups g ON g.id=q.group_id LEFT JOIN users us ON us.id=q.created_by
                       WHERE q.status IN ('queued','error') OR q.started_at > ?
                       ORDER BY CASE q.status WHEN 'queued' THEN 0 WHEN 'error' THEN 1 ELSE 2 END,
                                q.position, q.id""", (time.time() - 86400,))
    out = []
    for r in rows:
        if not queue_visible(u, r):
            continue
        meta = json.loads(r.pop("meta") or "{}")
        pl = next((x for x in meta.get("plates", []) if x["index"] == r["plate"]), {})
        r["prediction_s"] = pl.get("prediction_s", 0)
        r["weight_g"] = pl.get("weight_g", 0)
        r["filaments"] = pl.get("filaments", [])
        out.append(r)
    return out


@app.post("/api/queue")
async def queue_add(request: Request, body: dict):
    u = U(request)
    need_operator(u)
    file_for(u, int(body["file_id"]))
    sn = body.get("printer_sn") or None
    gid = int(body["group_id"]) if body.get("group_id") else None
    if sn:
        printer_for(u, sn)
        gid = None
    elif gid:
        if not group_targetable(u, gid):
            raise HTTPException(403, _("Non hai accesso a tutte le stampanti di questo gruppo"))
    elif not u.is_admin:
        raise HTTPException(400, _("Scegli una stampante o un gruppo"))
    pos = (S().db.one("SELECT MAX(position) m FROM queue")["m"] or 0) + 1
    qid = S().db.x("INSERT INTO queue(file_id, plate, printer_sn, group_id, mapping, options, position, created_at, "
                   "copies, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (int(body["file_id"]), int(body.get("plate", 1)), sn, gid,
                    json.dumps(body["slots"]) if (body.get("slots") and sn) else None,
                    json.dumps(body.get("options") or {}), pos, time.time(),
                    max(1, int(body.get("copies", 1))), u.id))
    return {"id": qid}


def _queue_item(u: User, qid: int) -> dict:
    r = S().db.one("SELECT q.*, f.project_id FROM queue q LEFT JOIN files f ON f.id=q.file_id WHERE q.id=?", (qid,))
    if not r or not queue_visible(u, r):
        raise HTTPException(404)
    need_operator(u)
    return r


@app.delete("/api/queue/{qid}")
async def queue_delete(request: Request, qid: int):
    _queue_item(U(request), qid)
    S().db.x("DELETE FROM queue WHERE id=?", (qid,))
    return {"ok": True}


@app.post("/api/queue/{qid}/retry")
async def queue_retry(request: Request, qid: int):
    _queue_item(U(request), qid)
    S().db.x("UPDATE queue SET status='queued', message='' WHERE id=?", (qid,))
    return {"ok": True}


@app.post("/api/queue/{qid}/move")
async def queue_move(request: Request, qid: int, body: dict):
    _queue_item(U(request), qid)
    items = S().db.q("SELECT id FROM queue WHERE status='queued' ORDER BY position, id")
    ids = [i["id"] for i in items]
    if qid not in ids:
        raise HTTPException(404)
    i = ids.index(qid)
    j = max(0, min(len(ids) - 1, i + (-1 if body.get("dir") == "up" else 1)))
    ids[i], ids[j] = ids[j], ids[i]
    for n, x in enumerate(ids):
        S().db.x("UPDATE queue SET position=? WHERE id=?", (n, x))
    return {"ok": True}


@app.post("/api/queue/pause")
async def queue_pause(request: Request, body: dict):
    need_admin(U(request))
    S().db.set_setting("queue_paused", bool(body.get("paused")))
    return {"paused": bool(body.get("paused"))}


# ====================================================================== storico e statistiche
@app.get("/api/jobs")
async def jobs(request: Request, limit: int = 200, printer: str | None = None):
    u = U(request)
    rows = S().db.q("""SELECT j.*, us.username AS started_by_name FROM jobs j LEFT JOIN users us ON us.id=j.started_by
                       ORDER BY j.started_at DESC LIMIT ?""", (limit * 3 if not u.is_admin else limit,))
    rows = [r for r in rows if u.sees_printer(r["printer_sn"]) and (not printer or r["printer_sn"] == printer)][:limit]
    fil = job_filaments([r["id"] for r in rows])
    for r in rows:
        r.pop("filaments", None)
        r["filaments"] = fil.get(r["id"], [])
    return rows


def job_filaments(job_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for i in range(0, len(job_ids), 500):
        chunk = job_ids[i:i + 500]
        if not chunk:
            continue
        for f in S().db.q(f"SELECT * FROM job_filaments WHERE job_id IN ({','.join('?' * len(chunk))}) ORDER BY id", chunk):
            out.setdefault(f["job_id"], []).append(
                {k: f.get(k) for k in ("type", "brand", "color", "grams", "meters", "ams_id", "slot_id", "source")})
    return out


def filament_totals(where: str, args: list) -> list[dict]:
    """Consumo raggruppato per tipo, marca e colore nel periodo."""
    w = where.replace("started_at", "j.started_at").replace("printer_sn", "j.printer_sn")
    return S().db.q(f"""SELECT f.type, f.brand, f.color, ROUND(SUM(f.grams), 1) grams, ROUND(SUM(f.meters), 1) meters,
                        COUNT(DISTINCT f.job_id) prints
                        FROM job_filaments f JOIN jobs j ON j.id=f.job_id WHERE {w}
                        GROUP BY f.type, f.brand, f.color ORDER BY grams DESC""", args)


@app.delete("/api/jobs/{jid}")
async def job_delete(request: Request, jid: int):
    need_admin(U(request))
    j = S().db.one("SELECT snapshot FROM jobs WHERE id=?", (jid,))
    if j and j["snapshot"]:
        (cfg.data_path / j["snapshot"]).unlink(missing_ok=True)
    S().db.x("DELETE FROM jobs WHERE id=?", (jid,))
    return {"ok": True}


@app.get("/api/jobs/{jid}/snapshot")
async def job_snapshot(request: Request, jid: int):
    j = S().db.one("SELECT snapshot, printer_sn FROM jobs WHERE id=?", (jid,))
    if not j or not U(request).sees_printer(j["printer_sn"]) or not j["snapshot"] \
            or not (cfg.data_path / j["snapshot"]).exists():
        raise HTTPException(404)
    return FileResponse(cfg.data_path / j["snapshot"], media_type="image/jpeg")


def _stats_filter(u: User, days: int, group: int | None) -> tuple[str, list]:
    """Clausola WHERE per le stampe visibili all'utente nel periodo (e nel gruppo scelto)."""
    sns = None if u.is_admin else set(u.printers)
    if group:
        members = auth.group_members().get(group, set())
        sns = members if sns is None else sns & members
    where, args = "started_at>=?", [time.time() - days * 86400]
    if sns is not None:
        if not sns:
            sns = {"__nessuna__"}
        where += f" AND printer_sn IN ({','.join('?' * len(sns))})"
        args += sorted(sns)
    return where, args


@app.get("/api/stats/export.xlsx")
async def stats_export(request: Request, days: int = 30, group: int | None = None):
    from .export import build_stats_xlsx
    u = U(request)
    days = max(1, min(3650, days))
    where, args = _stats_filter(u, days, group)
    jobs = S().db.q(f"""SELECT j.*, us.username AS started_by_name FROM jobs j
                        LEFT JOIN users us ON us.id=j.started_by WHERE {where.replace('started_at', 'j.started_at')
                        .replace('printer_sn', 'j.printer_sn')} ORDER BY j.started_at""", args)
    gname = S().db.one("SELECT name FROM printer_groups WHERE id=?", (group,)) if group else None
    scope = (_("Gruppo {g}", g=gname["name"]) if gname else
             (_("Tutte") if u.is_admin else _("Tutte quelle assegnate all'utente")))
    data = await asyncio.to_thread(build_stats_xlsx, jobs, filaments=filament_totals(where, args),
                                   days=days, scope=scope, user=u.username,
                                   lang=current_lang.get())
    fname = f"fleetbuddy-stats-{time.strftime('%Y%m%d')}-{days}d.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


def _stats_data(u: User, days: int, group: int | None) -> dict:
    db = S().db
    where, args = _stats_filter(u, days, group)
    tot = db.one(f"""SELECT COUNT(*) n, SUM(status='finished') ok, SUM(status='failed') ko,
                    SUM(status='cancelled') cancelled, COALESCE(SUM(filament_g),0) grams,
                    COALESCE(SUM(COALESCE(ended_at, strftime('%s','now')) - started_at),0) secs
                    FROM jobs WHERE {where}""", args)
    per = db.q(f"""SELECT printer_sn, MAX(printer_name) name, COUNT(*) n, SUM(status='finished') ok,
                  SUM(status='failed') ko, SUM(status='cancelled') cancelled, COALESCE(SUM(filament_g),0) grams,
                  COALESCE(SUM(COALESCE(ended_at, strftime('%s','now')) - started_at),0) secs
                  FROM jobs WHERE {where} GROUP BY printer_sn ORDER BY n DESC""", args)
    daily = db.q(f"""SELECT date(started_at,'unixepoch','localtime') day, COUNT(*) n,
                    SUM(status='finished') ok, COALESCE(SUM(filament_g),0) grams
                    FROM jobs WHERE {where} GROUP BY day ORDER BY day""", args)
    return {"days": days, "total": tot, "per_printer": per, "daily": daily, "filaments": filament_totals(where, args)}


@app.get("/api/stats")
async def stats(request: Request, days: int = 30, group: int | None = None):
    return _stats_data(U(request), days, group)


def _stats_jobs(u: User, days: int, group: int | None) -> list[dict]:
    where, args = _stats_filter(u, days, group)
    w = where.replace("started_at", "j.started_at").replace("printer_sn", "j.printer_sn")
    rows = S().db.q(f"""SELECT j.*, us.username AS started_by_name FROM jobs j
                        LEFT JOIN users us ON us.id=j.started_by WHERE {w} ORDER BY j.started_at""", args)
    fil = job_filaments([r["id"] for r in rows])
    for r in rows:
        r["filaments"] = fil.get(r["id"], [])
    return rows


def _scope_label(u: User, group: int | None) -> str:
    gname = S().db.one("SELECT name FROM printer_groups WHERE id=?", (group,)) if group else None
    return (_("Gruppo {g}", g=gname["name"]) if gname else
            (_("Tutte") if u.is_admin else _("Tutte quelle assegnate all'utente")))


def _download(data: bytes, media: str, days: int, ext: str, kind: str = "") -> Response:
    fname = f"fleetbuddy-stats{'-' + kind if kind else ''}-{time.strftime('%Y%m%d')}-{days}d.{ext}"
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


@app.get("/api/stats/export.json")
async def stats_export_json(request: Request, days: int = 30, group: int | None = None):
    """Tutto in un unico file: riepilogo, per stampante, per giorno, filamenti e dettaglio delle stampe."""
    u = U(request)
    days = max(1, min(3650, days))
    data = _stats_data(u, days, group)
    jobs = _stats_jobs(u, days, group)
    keep = ("id", "printer_sn", "printer_name", "task_name", "status", "progress", "started_at", "ended_at",
            "filament_g", "error", "started_by_name", "filaments")
    out = {"app": APP_NAME, "version": __version__, "generated_at": int(time.time()), "generated_by": u.username,
           "period_days": days, "scope": _scope_label(u, group), **data,
           "jobs": [{k: j.get(k) for k in keep} for j in jobs]}
    return _download(json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8"),
                     "application/json; charset=utf-8", days, "json")


@app.get("/api/stats/export.csv")
async def stats_export_csv(request: Request, kind: str = "jobs", days: int = 30, group: int | None = None):
    """CSV di una tabella: jobs (dettaglio stampe), filaments, printers, daily."""
    from .export import build_csv
    u = U(request)
    days = max(1, min(3650, days))
    if kind not in ("jobs", "filaments", "printers", "daily"):
        raise HTTPException(400, _("Tipo di esportazione non valido"))
    data = _stats_data(u, days, group)
    lang = current_lang.get()
    if kind == "jobs":
        rows = _stats_jobs(u, days, group)
    else:
        rows = {"filaments": data["filaments"], "printers": data["per_printer"], "daily": data["daily"]}[kind]
    return _download(build_csv(kind, rows, lang), "text/csv; charset=utf-8", days, "csv", kind)


# ====================================================================== Hub (solo admin)
def _admin(request: Request):
    need_admin(U(request))


@app.get("/api/hub")
async def hub_info(request: Request):
    _admin(request)
    info = await asyncio.to_thread(S().hub.captain)
    try:
        info["ntp"] = await asyncio.to_thread(S().hub.get_ntp)
    except HubError:
        info["ntp"] = None
    return info


@app.put("/api/hub/ntp")
async def hub_ntp(request: Request, body: dict):
    _admin(request)
    return await asyncio.to_thread(S().hub.set_ntp, {k: v for k, v in body.items() if v not in (None, "")})


def _with_fw_check(printers: list[dict]) -> list[dict]:
    for p in printers:
        p["fw_min"] = firmware_below_min(p.get("model", ""), p.get("version", ""))
    return printers


@app.get("/api/hub/discover")
async def hub_discover(request: Request, only_can_bind: bool = False):
    _admin(request)
    return _with_fw_check(await asyncio.to_thread(S().hub.local_printers, only_can_bind))


@app.get("/api/hub/scan")
async def hub_scan(request: Request, prefix: str, begin: int = 1, end: int = 254):
    _admin(request)
    return _with_fw_check(await asyncio.to_thread(S().hub.scan_printers, prefix, begin, end))


@app.post("/api/hub/bind")
async def hub_bind(request: Request, body: dict):
    _admin(request)
    return await asyncio.to_thread(S().hub.bind, body["dev_id"], body["dev_ip"])


@app.post("/api/hub/unbind")
async def hub_unbind(request: Request, body: dict):
    _admin(request)
    return await asyncio.to_thread(S().hub.unbind, body["dev_ids"])


@app.get("/api/hub/cache")
async def hub_cache(request: Request):
    _admin(request)
    files = await asyncio.to_thread(S().hub.cached_files, 500)
    names = {r["md5"]: r["filename"] for r in S().db.q("SELECT md5, filename FROM files")}
    for f in files:
        f["filename"] = names.get(f.get("file_hash"), "")
    return files


def _hub_password_problem(pwd: str) -> str | None:
    if len(pwd) < 8 or len(pwd.encode()) > 64:
        return _("La password deve avere da 8 a 64 caratteri")
    for rx, what in ((r"[A-Z]", _("una maiuscola")), (r"[a-z]", _("una minuscola")), (r"\d", _("un numero")),
                     (r"[^A-Za-z0-9]", _("un carattere speciale"))):
        if not re.search(rx, pwd):
            return _("La password deve contenere {what}", what=what)
    if pwd == cfg.hub.password:
        return _("Usa una password diversa da quella dell'account API")
    return None


@app.post("/api/hub/web_user")
async def hub_web_user_add(request: Request, body: dict):
    _admin(request)
    user, pwd = str(body.get("username", "")).strip(), str(body.get("password", ""))
    if not 1 < len(user) < 32:
        raise HTTPException(400, _("Il nome utente deve avere da 2 a 31 caratteri"))
    problem = _hub_password_problem(pwd)
    if problem:
        raise HTTPException(400, problem)
    return await asyncio.to_thread(S().hub.add_web_user, user, pwd)


@app.delete("/api/hub/web_user")
async def hub_web_user_delete(request: Request):
    _admin(request)
    return await asyncio.to_thread(S().hub.delete_web_user)


@app.get("/api/hub/firmwares")
async def hub_firmwares(request: Request):
    _admin(request)
    return await asyncio.to_thread(S().hub.firmwares)


@app.post("/api/hub/firmwares")
async def hub_firmware_upload(request: Request, file: UploadFile = File(...)):
    _admin(request)
    tmp = cfg.data_path / "tmp" / Path(file.filename or "firmware.zip").name
    with tmp.open("wb") as out:
        shutil.copyfileobj(file.file, out, 1 << 20)
    try:
        md5 = await asyncio.to_thread(md5_file, tmp)
        return await asyncio.to_thread(S().hub.upload_firmware, str(tmp), tmp.name, md5)
    finally:
        tmp.unlink(missing_ok=True)


@app.delete("/api/hub/firmwares")
async def hub_firmware_delete(request: Request, body: dict):
    _admin(request)
    return await asyncio.to_thread(S().hub.delete_firmwares, body["firmwares"])


@app.post("/api/hub/upgrade")
async def hub_upgrade(request: Request, body: dict):
    u = U(request)
    _admin(request)
    for sn in body["dev_ids"]:
        p = printer_for(u, sn)
        if p["active"]:
            raise HTTPException(409, _("{p} sta stampando: aggiornamento non consentito", p=p["name"]))
    return await asyncio.to_thread(S().hub.upgrade, body["dev_ids"], body["firmware_name"])


# ====================================================================== statico
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html", headers={"Cache-Control": "no-cache"})
