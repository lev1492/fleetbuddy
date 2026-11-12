"""Produzione: ordini con più modelli e copie, assegnati automaticamente alle stampanti scelte.

Ogni copia di un piatto è un "pezzo" (production_units). I pezzi in attesa sono ordinati per
priorità dell'ordine, posizione dell'ordine (scambiabile dall'utente) e sequenza; quando una stampante
abilitata è pronta, prende il primo pezzo compatibile. Il pianificatore simula lo stesso criterio nel tempo,
partendo dallo stato reale delle stampanti (stampa in corso, piatto da liberare, coda manuale), e produce
il Gantt, la saturazione delle stampanti e la fine stimata di ogni ordine.

Un pezzo fallito o annullato torna in attesa (se l'ordine lo prevede) davanti agli altri pezzi del suo
ordine: il piano slitta di conseguenza.
"""
from __future__ import annotations

import json
import re
import zipfile
import logging
import time

from .hub_client import HubError
from .printers import is_ready, resolve_slots

log = logging.getLogger("fleetbuddy.production")

PRIORITIES = (0, 1, 2, 3)            # bassa, normale, alta, urgente
PALETTE = ["#3B82F6", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316", "#6366F1", "#84CC16"]
DEFAULT_PRINT_S = 3600               # durata usata quando il file non indica il tempo stimato

SCHEMA = """
CREATE TABLE IF NOT EXISTS productions (
    id INTEGER PRIMARY KEY, name TEXT, priority INTEGER DEFAULT 1, position REAL, color TEXT,
    status TEXT DEFAULT 'active', printers TEXT, options TEXT, auto_retry INTEGER DEFAULT 1,
    created_by INTEGER, created_at REAL, completed_at REAL, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS production_units (
    id INTEGER PRIMARY KEY, production_id INTEGER, file_id INTEGER, plate INTEGER, seq INTEGER,
    status TEXT DEFAULT 'pending', printer_sn TEXT, job_id INTEGER, attempts INTEGER DEFAULT 0,
    message TEXT DEFAULT '', started_at REAL, ended_at REAL
);
CREATE INDEX IF NOT EXISTS pu_prod ON production_units(production_id);
CREATE INDEX IF NOT EXISTS pu_job ON production_units(job_id);
"""


def init(db):
    db._conn.executescript(SCHEMA)
    db._conn.commit()


def N_(s: str) -> str:
    """Segna una frase da tradurre."""
    return s


def _msg(key: str, **params) -> str:
    return json.dumps({"msg": key, "params": params}, ensure_ascii=False)


# ---------------------------------------------------------------------- compatibilità e durate
def _norm_model(name: str) -> str:
    """Nome di modello confrontabile: "Bambu Lab H2D Pro" → "H2DPRO", "X1 Carbon" → "X1C"."""
    n = re.sub(r"[^A-Z0-9]", "", (name or "").upper().replace("BAMBU LAB", ""))
    return {"X1CARBON": "X1C", "BAMBULABX1CARBON": "X1C"}.get(n, n)


def file_printer(svc, f: dict) -> tuple[str, list[float]]:
    """Modello di stampante e diametri degli ugelli per cui il file è stato preparato (dalle impostazioni del
    progetto Bambu Studio / OrcaSlicer nel 3mf). Letti una volta dal file e memorizzati nei suoi dati."""
    meta = f.get("meta") or {}
    if "printer_model" not in meta and meta.get("kind") != "gcode":
        model, noz = "", []
        try:
            with zipfile.ZipFile(svc.cfg.path(f["path"])) as z:
                if "Metadata/project_settings.config" in z.namelist():
                    ps = json.loads(z.read("Metadata/project_settings.config"))
                    model = str(ps.get("printer_model") or "")
                    nd = ps.get("nozzle_diameter") or []
                    noz = [float(x) for x in (nd if isinstance(nd, list) else [nd]) if str(x).strip()]
        except Exception:  # noqa: BLE001 - file illeggibile o senza impostazioni: compatibilità non verificabile
            pass
        meta["printer_model"], meta["nozzle_diameters_list"] = model, noz
        f["meta"] = meta
        if f.get("id"):
            svc.db.x("UPDATE files SET meta=? WHERE id=?", (json.dumps(meta), f["id"]))
    return meta.get("printer_model") or "", meta.get("nozzle_diameters_list") or []


def check(svc, f: dict, sn: str) -> tuple[bool, str]:
    """Il file può andare su questa stampante? (sì/no, motivo). Tipo di file (.gcode.3mf → Bambu; .gcode/.bgcode →
    stampanti dirette che accettano file; .ufp → UltiMaker), modello per cui è stato preparato, diametro dell'ugello."""
    kind, fmt = f["meta"].get("kind"), f["meta"].get("format")
    c = svc.direct.get(sn)
    if kind != "gcode":
        if c is not None:
            return False, N_("file per stampanti Bambu Lab")
        p = svc.printers.get(sn) or {}
        model, noz = file_printer(svc, f)
        pm = p.get("model") or ""
        if model and pm and _norm_model(model) != _norm_model(pm):
            return False, _msg("preparato per {model}", model=model.replace("Bambu Lab ", ""))
        have = {round(float(n["diameter"]), 2) for n in p.get("nozzles") or [] if n.get("diameter")}
        need = {round(x, 2) for x in noz}
        if need and have and not (need & have):
            return False, _msg("ugello {have} mm, il file richiede {need} mm",
                               have=" / ".join(f"{x:g}" for x in sorted(have)), need=" / ".join(f"{x:g}" for x in sorted(need)))
        return True, ""
    if c is None:
        return False, N_("file per stampanti Klipper, Duet, PrusaLink…")
    if not c.caps.get("upload", True):
        return False, N_("la stampante non accetta file dalla rete")
    if fmt == "ufp" and not c.caps.get("ufp"):
        return False, N_("file .ufp: solo UltiMaker")
    return True, ""


def reason_text(why: str) -> str:
    """Motivo di incompatibilità come testo (per i messaggi della coda)."""
    try:
        d = json.loads(why)
        return d["msg"].format(**d.get("params", {}))
    except (ValueError, TypeError, KeyError):
        return why


def compatible(svc, f: dict, sn: str) -> bool:
    return check(svc, f, sn)[0]


def plate_of(f: dict, plate: int) -> dict:
    return next((x for x in f["meta"].get("plates", []) if x["index"] == plate), (f["meta"].get("plates") or [{}])[0])


def duration_s(f: dict | None, plate: int) -> int:
    if not f:
        return DEFAULT_PRINT_S
    return int(plate_of(f, plate).get("prediction_s") or DEFAULT_PRINT_S)


def changeover_s(db) -> int:
    return int(float(db.get_setting("production_changeover_min", 10) or 0) * 60)


# ---------------------------------------------------------------------- ordini
def create(db, body: dict, user_id: int | None) -> int:
    items = [it for it in body.get("items") or [] if int(it.get("copies") or 0) > 0 and it.get("file_id")]
    if not items:
        raise ValueError(N_("Aggiungi almeno un modello con almeno una copia"))
    printers = [str(x) for x in body.get("printers") or []]
    if not printers:
        raise ValueError(N_("Scegli almeno una stampante"))
    pos = (db.one("SELECT MAX(position) AS m FROM productions") or {}).get("m") or 0
    used = {r["color"] for r in db.q("SELECT color FROM productions WHERE status IN ('active','paused')")}
    color = body.get("color") or next((c for c in PALETTE if c not in used), PALETTE[int(pos) % len(PALETTE)])
    pid = db.x("INSERT INTO productions(name, priority, position, color, status, printers, options, auto_retry, created_by, "
               "created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(body.get("name") or "").strip()[:80] or "Produzione", max(0, min(3, int(body.get("priority", 1)))),
                pos + 1, color, "paused" if body.get("paused") else "active", json.dumps(printers),
                json.dumps(body.get("options") or {}), 1 if body.get("auto_retry", True) else 0, user_id, time.time()))
    seq = 0
    for it in items:
        for _ in range(min(999, int(it["copies"]))):
            seq += 1
            db.x("INSERT INTO production_units(production_id, file_id, plate, seq) VALUES(?,?,?,?)",
                 (pid, int(it["file_id"]), int(it.get("plate") or 1), seq))
    log.info("Produzione %s creata: %d pezzi su %d stampanti", pid, seq, len(printers))
    return pid


def ordered_pending(db, include_paused=False) -> list[dict]:
    """Pezzi in attesa nell'ordine in cui verranno stampati."""
    st = "('active','paused')" if include_paused else "('active')"
    return db.q(f"""SELECT u.*, p.priority, p.position, p.printers, p.options, p.created_by, p.name AS prod_name,
                          p.color, p.status AS prod_status
                   FROM production_units u JOIN productions p ON p.id=u.production_id
                   WHERE u.status='pending' AND p.status IN {st}
                   ORDER BY p.priority DESC, p.position, u.seq""")


def ordered_productions(db) -> list[dict]:
    return db.q("SELECT * FROM productions ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 0 ELSE 1 END, "
                "priority DESC, position, id")


def move(db, pid: int, direction: str):
    """Scambia l'ordine con quello prima o dopo. Se il vicino ha un'altra priorità, l'ordine ne prende la priorità
    (così "sali" porta davvero davanti, anche oltre il confine di priorità)."""
    rows = [r for r in ordered_productions(db) if r["status"] in ("active", "paused")]
    idx = next((i for i, r in enumerate(rows) if r["id"] == pid), None)
    if idx is None:
        return
    j = idx - 1 if direction == "up" else idx + 1
    if not 0 <= j < len(rows):
        return
    me, other = rows[idx], rows[j]
    if me["priority"] != other["priority"]:
        db.x("UPDATE productions SET priority=? WHERE id=?", (other["priority"], pid))
    db.x("UPDATE productions SET position=? WHERE id=?", (other["position"], me["id"]))
    db.x("UPDATE productions SET position=? WHERE id=?", (me["position"], other["id"]))


def summary(db, pid: int) -> dict:
    counts = {r["status"]: r["n"] for r in db.q(
        "SELECT status, COUNT(*) AS n FROM production_units WHERE production_id=? GROUP BY status", (pid,))}
    return {"total": sum(counts.values()), "done": counts.get("done", 0), "pending": counts.get("pending", 0),
            "printing": counts.get("printing", 0) + counts.get("starting", 0), "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0), "review": counts.get("review", 0)}


def _check_completed(svc, pid: int):
    s = summary(svc.db, pid)
    if s["pending"] or s["printing"] or s["review"]:
        return
    row = svc.db.one("SELECT name, status FROM productions WHERE id=?", (pid,))
    if row and row["status"] in ("active", "paused"):
        svc.db.x("UPDATE productions SET status='completed', completed_at=? WHERE id=?", (time.time(), pid))
        svc.toast("Produzione completata: {name} ({n} pezzi)", "ok", name=row["name"], n=s["done"])


# ---------------------------------------------------------------------- eventi delle stampe
def on_job_started(db, unit_id: int | None, job_id: int, sn: str):
    if unit_id:
        db.x("UPDATE production_units SET status='printing', job_id=?, printer_sn=?, started_at=?, message='' WHERE id=?",
             (job_id, sn, time.time(), unit_id))


def on_job_closed(svc, job_id: int, status: str, printer_name: str):
    u = svc.db.one("SELECT u.*, p.auto_retry, p.name AS prod_name FROM production_units u "
                   "JOIN productions p ON p.id=u.production_id WHERE u.job_id=?", (job_id,))
    if not u:
        return
    if status == "to_confirm":
        # stampante offline oltre il tempo previsto: il pezzo attende la conferma dell'esito (non va ripianificato)
        svc.db.x("UPDATE production_units SET status='review', message=? WHERE id=?",
                 (_msg("stampante offline: conferma l'esito della stampa"), u["id"]))
        return
    if status == "finished":
        svc.db.x("UPDATE production_units SET status='done', ended_at=? WHERE id=?", (time.time(), u["id"]))
    elif u["auto_retry"]:
        # torna in attesa davanti agli altri pezzi del suo ordine: il piano slitta
        svc.db.x("UPDATE production_units SET status='pending', job_id=NULL, printer_sn=NULL, attempts=attempts+1, "
                 "message=? WHERE id=?", (_msg("non riuscita su {printer}: verrà rilanciata", printer=printer_name), u["id"]))
        # l'ordine poteva risultare completato (es. pezzo chiuso per stima e poi risultato interrotto): si riapre
        svc.db.x("UPDATE productions SET status='active', completed_at=NULL WHERE id=? AND status='completed'", (u["production_id"],))
        svc.toast("Produzione {name}: pezzo non riuscito su {printer}, verrà rilanciato", "error",
                  name=u["prod_name"], printer=printer_name)
    else:
        svc.db.x("UPDATE production_units SET status='failed', ended_at=?, message=? WHERE id=?",
                 (time.time(), _msg("non riuscita su {printer}", printer=printer_name), u["id"]))
    _check_completed(svc, u["production_id"])


def on_not_started(db, unit_id: int, reason: str):
    db.x("UPDATE production_units SET status='pending', printer_sn=NULL, job_id=NULL, attempts=attempts+1, message=? "
         "WHERE id=? AND status='starting'", (reason, unit_id))


def reconcile(svc):
    """All'avvio: pezzi rimasti "in invio" senza stampa in arrivo tornano in attesa; pezzi "in stampa" la cui stampa
    si è chiusa mentre Fabbrix Farm Manager era spento vengono aggiornati."""
    db = svc.db
    for u in db.q("SELECT * FROM production_units WHERE status='starting'"):
        if not (u["printer_sn"] and svc.has_pending(u["printer_sn"])):
            on_not_started(db, u["id"], _msg("invio interrotto: verrà ritentato"))
    for u in db.q("SELECT u.id, u.job_id, j.status, j.printer_name FROM production_units u LEFT JOIN jobs j ON j.id=u.job_id "
                  "WHERE u.status IN ('printing', 'review')"):
        if u["status"] and u["status"] != "running":
            on_job_closed(svc, u["job_id"], u["status"], u["printer_name"] or "")


# ---------------------------------------------------------------------- conferma dell'operatore
# Fuori orario la produzione non avvia pezzi da sola. Una stampante la cui stampa è finita fuori orario ha
# probabilmente ancora il pezzo sul piatto: resta "in attesa di conferma" e riparte solo dopo l'OK di un operatore
# (pulsante in Produzione, oppure "Piatto liberato" sulle Bambu). Con "Consenti l'avvio delle stampe fuori orario"
# l'OK vale subito anche fuori orario; altrimenti il pezzo parte con l'orario lavorativo.
def holds(db) -> dict:
    return db.get_setting("prod_hold", {}) or {}


def oks(db) -> dict:
    return db.get_setting("prod_go", {}) or {}


def hold_printer(svc, sn: str, at: float):
    """Stampa finita fuori orario: la stampante attende la conferma dell'operatore."""
    cal = svc.workcal()
    if not cal.active() or cal.is_working(at):
        return
    h = holds(svc.db)
    h[sn] = at
    svc.db.set_setting("prod_hold", h)


def operator_ok(svc, sn: str):
    """OK dell'operatore: il piatto è libero e c'è qualcuno. Toglie l'attesa e consente il prossimo pezzo."""
    h, g = holds(svc.db), oks(svc.db)
    h.pop(sn, None)
    g[sn] = time.time()
    svc.db.set_setting("prod_hold", h)
    svc.db.set_setting("prod_go", g)


def clear_printer(db, sn: str):
    """Una stampa è partita sulla stampante (da Fabbrix Farm Manager o da qualcuno alla stampante): attesa e OK esauriti."""
    h, g = holds(db), oks(db)
    if sn in h or sn in g:
        h.pop(sn, None)
        g.pop(sn, None)
        db.set_setting("prod_hold", h)
        db.set_setting("prod_go", g)


def waiting(svc, user=None) -> list[dict]:
    """Stampanti pronte con pezzi da fare che aspettano l'operatore (stampa finita fuori orario, o fuori orario adesso)."""
    db, cal, now = svc.db, svc.workcal(), time.time()
    if not cal.active():
        return []
    working = cal.is_working(now)
    h, g = holds(db), oks(db)
    files: dict[int, dict | None] = {}
    wanted: set[str] = set()
    for u in ordered_pending(db):
        f = files.setdefault(u["file_id"], db.file(u["file_id"]))
        if f:
            wanted |= {sn for sn in json.loads(u["printers"] or "[]") if compatible(svc, f, sn)}
    out = []
    for sn in sorted(wanted):
        p = svc.printers.get(sn)
        if not p or (user and not user.sees_printer(sn)) or not is_ready(p) or svc.has_pending(sn):
            continue
        if sn in g:
            out.append({"sn": sn, "name": p["name"], "reason": "ok", "since": g[sn], "can_start": False})
            continue
        if sn in h or not working:
            out.append({"sn": sn, "name": p["name"], "reason": "hold" if sn in h else "offhours", "since": h.get(sn),
                        "can_start": working or cal.cfg.get("allow_offhours_start", False)})
    return out


# ---------------------------------------------------------------------- assegnazione automatica
async def dispatch(svc):
    """Avvia i pezzi in attesa sulle stampanti abilitate e pronte. La coda manuale ha la precedenza: una stampante con
    voci della coda indirizzate a lei non riceve pezzi finché quelle non sono partite."""
    db = svc.db
    units = ordered_pending(db)
    if not units:
        return
    cal = svc.workcal()
    now = time.time()
    working = not cal.active() or cal.is_working(now)
    h, g = holds(db), oks(db) if cal.active() else {}
    allow_off = cal.cfg.get("allow_offhours_start", False)

    def allowed(sn: str) -> bool:
        if not cal.active():
            return True
        if sn in g:                                  # OK dell'operatore: subito in orario, fuori orario se consentito
            return working or allow_off
        return working and sn not in h               # automatico solo in orario e se non attende conferma
    if cal.active() and not working and not g:
        return                                       # fuori orario e nessun OK: nessun nuovo pezzo
    queue_targets = {r["printer_sn"] for r in db.q("SELECT printer_sn FROM queue WHERE status='queued' AND printer_sn IS NOT NULL")}
    if not db.get_setting("queue_paused", False):
        queue_any = db.one("SELECT COUNT(*) AS n FROM queue WHERE status='queued' AND printer_sn IS NULL") or {}
    else:
        queue_any = {"n": 0}
    busy: set[str] = set()
    files: dict[int, dict | None] = {}
    for u in units:
        f = files.setdefault(u["file_id"], db.file(u["file_id"]))
        if not f:
            db.x("UPDATE production_units SET status='failed', message=? WHERE id=?", (_msg("file rimosso dall'archivio"), u["id"]))
            continue
        pl = plate_of(f, u["plate"])
        fits = not cal.active() or cal.can_start_now(now, duration_s(f, u["plate"]))
        note = None
        for sn in json.loads(u["printers"] or "[]"):
            p = svc.printers.get(sn)
            if not allowed(sn) or (not fits and sn not in g):
                continue                             # attende l'operatore, oppure ("intera") non finirebbe in orario
            if not p or sn in busy or sn in queue_targets or not is_ready(p) or svc._cooldown.get(sn, 0) > time.time() \
                    or svc.has_pending(sn) or not compatible(svc, f, sn):
                continue
            if queue_any.get("n") and not svc.direct.get(sn):
                continue   # voci della coda per "qualsiasi stampante": prima quelle
            if sn in svc.direct:
                slots = []
            else:
                slots, _w = resolve_slots(None, pl.get("filaments") or [], p)
                if slots is None:
                    note = _msg("in attesa del filamento su {printer}", printer=p["name"])
                    continue
            try:
                await svc.start_print(u["file_id"], u["plate"], sn, slots, json.loads(u["options"] or "{}"),
                                      user_id=u["created_by"], queue={"unit": u["id"]})
            except HubError as e:
                svc._cooldown[sn] = time.time() + 120
                note = _msg("errore su {printer}: {e}", printer=p["name"], e=e.localized(svc.notify_lang))
                continue
            except Exception as e:  # noqa: BLE001 - un errore blocca solo questo pezzo
                log.exception("Produzione: invio del pezzo %s a %s non riuscito", u["id"], sn)
                svc._cooldown[sn] = time.time() + 120
                note = _msg("errore su {printer}: {e}", printer=p["name"], e=str(e)[:200])
                continue
            db.x("UPDATE production_units SET status='starting', printer_sn=?, message='' WHERE id=?", (sn, u["id"]))
            clear_printer(db, sn)
            busy.add(sn)
            note = None
            break
        if note is not None and note != u.get("message"):
            db.x("UPDATE production_units SET message=? WHERE id=?", (note, u["id"]))


# ---------------------------------------------------------------------- pianificazione (Gantt)
def plan(svc, user=None, draft: dict | None = None, horizon_h: float = 72, start: float | None = None) -> dict:
    """Simula la produzione nel tempo con lo stesso criterio dell'assegnazione reale.
    draft: ordine non ancora salvato, per l'anteprima (stesso formato della creazione)."""
    db = svc.db
    now = time.time()
    co = changeover_s(db)
    cal = svc.workcal()
    files: dict[int, dict | None] = {}

    def free_after(end: float) -> float:
        # il cambio piatto richiede una persona: avviene in orario lavorativo
        return (cal.next_working(end) if cal.active() else end) + co

    def file(fid):
        if fid not in files:
            files[fid] = db.file(fid)
        return files[fid]

    visible = [p for p in svc.printers.values() if user is None or user.sees_printer(p["sn"])]
    printers = {p["sn"]: p for p in visible}
    rows: dict[str, dict] = {}
    free_at: dict[str, float | None] = {}
    bars: list[dict] = []
    unit_of_job = {r["job_id"]: r for r in db.q(
        "SELECT u.id, u.job_id, u.production_id, p.color, p.name FROM production_units u JOIN productions p ON p.id=u.production_id "
        "WHERE u.status='printing'")}
    for sn, p in printers.items():
        state = p["state"] if p["online"] else "OFFLINE"
        rows[sn] = {"sn": sn, "name": p["name"], "online": p["online"], "state": state, "brand": p.get("brand", ""),
                    "model": p.get("printer_model") or p.get("model", "")}
        t = now
        job = svc._open_jobs.get(sn)
        if not p["online"]:
            # stampante offline: resta nel piano. Se stava stampando, la stampa continua a comparire stimata
            # dall'ultimo stato noto; i pezzi restano pianificati su di lei (si riallinea quando torna online)
            seen, last = svc._last_online.get(sn, (now, {}))
            rows[sn]["last_seen"] = seen
            if job:
                # fine stimata = ultimo contatto + tempo rimanente di allora (non si allunga col passare del tempo:
                # superata, il lavoro viene chiuso come concluso per stima)
                end = max(now, seen + (last.get("remaining_min") or 0) * 60)
                u = unit_of_job.get(job.get("id"))
                bars.append({"sn": sn, "start": job.get("started_at") or seen, "end": end, "kind": "current", "offline": True,
                             "label": job.get("task_name") or last.get("task_name") or "", "progress": last.get("progress", 0),
                             "production_id": u["production_id"] if u else None, "color": u["color"] if u else None})
                t = free_after(end)
            free_at[sn] = t
            continue
        if sn in holds(db) and cal.active():
            rows[sn]["waiting_ok"] = True
            t = max(t, cal.next_working(now))
        if p["active"]:
            rem = (p.get("remaining_min") or 0) * 60
            start = (job or {}).get("started_at") or now - 60
            end = now + max(rem, 60)
            u = unit_of_job.get((job or {}).get("id"))
            bars.append({"sn": sn, "start": start, "end": end, "kind": "current", "label": p.get("task_name") or "",
                         "progress": p.get("progress", 0), "production_id": u["production_id"] if u else None,
                         "color": u["color"] if u else None, "paused": p["state"] == "PAUSE"})
            t = free_after(end)
        elif p["state"] in ("FINISH", "FAILED"):
            cw = cal.next_working(now) if cal.active() else now
            bars.append({"sn": sn, "start": cw, "end": cw + co, "kind": "clear", "label": ""})
            t = cw + co
        elif svc.has_pending(sn):
            pend = svc._take_pending(sn, consume=False) or {}
            f = file(pend.get("file_id")) if pend.get("file_id") else None
            d = duration_s(f, pend.get("plate") or 1)
            uid = (pend.get("queue") or {}).get("unit")
            u = db.one("SELECT p.color, p.id FROM production_units x JOIN productions p ON p.id=x.production_id WHERE x.id=?", (uid,)) if uid else None
            bars.append({"sn": sn, "start": now, "end": now + d, "kind": "starting", "label": pend.get("task_name") or "",
                         "production_id": u["id"] if u else None, "color": u["color"] if u else None})
            t = free_after(now + d)
        free_at[sn] = t
    # coda manuale indirizzata a una stampante: ha la precedenza
    for q in db.q("SELECT * FROM queue WHERE status='queued' AND printer_sn IS NOT NULL ORDER BY position, id"):
        sn = q["printer_sn"]
        if free_at.get(sn) is None:
            continue
        f = file(q["file_id"])
        for _ in range(max(1, q.get("copies") or 1)):
            d = duration_s(f, q["plate"])
            bars.append({"sn": sn, "start": free_at[sn], "end": free_at[sn] + d, "kind": "queue",
                         "label": f["filename"] if f else "?"})
            free_at[sn] = free_after(free_at[sn] + d)
    # pezzi della produzione (più l'eventuale ordine in anteprima)
    units = [dict(u) for u in ordered_pending(db)]
    if draft:
        dprio = int(draft.get("priority", 1))
        seq, extra = 0, []
        for it in draft.get("items") or []:
            for _ in range(min(999, int(it.get("copies") or 0))):
                seq += 1
                extra.append({"id": -seq, "production_id": 0, "file_id": int(it["file_id"]), "plate": int(it.get("plate") or 1),
                              "seq": seq, "priority": dprio, "position": 1e12, "printers": json.dumps(draft.get("printers") or []),
                              "color": "#94A3B8", "prod_name": draft.get("name") or "", "message": ""})
        units = sorted(units + extra, key=lambda u: (-u["priority"], u["position"], u["seq"]))
    eta: dict[int, float] = {}
    unplaced: dict[int, int] = {}
    for u in units:
        f = file(u["file_id"])
        cands = [sn for sn in json.loads(u["printers"] or "[]") if free_at.get(sn) is not None and f and compatible(svc, f, sn)]
        # a parità di tempo si preferisce una stampante online
        cands.sort(key=lambda s: not rows[s]["online"])
        if not cands:
            unplaced[u["production_id"]] = unplaced.get(u["production_id"], 0) + 1
            continue
        sn = min(cands, key=lambda s: (free_at[s], not rows[s]["online"], rows[s]["name"]))
        d = duration_s(f, u["plate"])
        start = cal.start_for(max(free_at[sn], now), d)
        bars.append({"sn": sn, "start": start, "end": start + d, "kind": "plan", "label": f["filename"] if f else "?",
                     "production_id": u["production_id"], "unit_id": u["id"], "color": u["color"],
                     "offline": not rows[sn]["online"]})
        free_at[sn] = free_after(start + d)
        eta[u["production_id"]] = max(eta.get(u["production_id"], 0), start + d)
    # pezzi in stampa: la fine stimata dell'ordine non può essere prima della loro fine
    for b in bars:
        if b.get("production_id") and b["kind"] in ("current", "starting"):
            eta[b["production_id"]] = max(eta.get(b["production_id"], 0), b["end"])
    horizon = now + horizon_h * 3600
    for sn, r in rows.items():
        busy = sum(max(0, min(b["end"], horizon) - max(b["start"], now)) for b in bars if b["sn"] == sn and b["kind"] != "clear")
        r["saturation"] = round(100 * busy / (horizon - now))
        r["free_at"] = free_at.get(sn)
        r["used"] = any(b["sn"] == sn for b in bars)
    # finestra visualizzata (scorrimento avanti e indietro nel tempo): stampe già fatte dallo storico
    win0 = start if start else now - 3600
    win1 = win0 + (horizon_h + 1) * 3600
    for r in db.q("""SELECT j.id, j.printer_sn, j.task_name, j.status, j.started_at, j.ended_at, j.estimated, j.progress,
                            u.production_id, pr.color, pr.name AS prod_name
                     FROM jobs j LEFT JOIN production_units u ON u.job_id=j.id LEFT JOIN productions pr ON pr.id=u.production_id
                     WHERE j.status!='running' AND j.started_at < ? AND j.ended_at > ?""", (win1, win0)):
        if r["printer_sn"] in rows:
            bars.append({"sn": r["printer_sn"], "start": r["started_at"], "end": r["ended_at"], "kind": "past",
                         "status": r["status"], "estimated": bool(r["estimated"]), "label": r["task_name"] or "",
                         "production_id": r["production_id"], "color": r["color"], "progress": r["progress"]})
    closed = cal.closed_spans(win0, win1) if cal.active() else []
    return {"now": now, "changeover_s": co, "horizon_h": horizon_h, "rows": list(rows.values()), "bars": bars,
            "closed": closed, "calendar_mode": cal.mode if cal.active() else "always", "window": [win0, win1],
            "eta": {str(k): v for k, v in eta.items()}, "unplaced": {str(k): v for k, v in unplaced.items()},
            "end": max([b["end"] for b in bars] + [now])}
