"""Libreria Materiali: inventario di produttori, filamenti e bobine (sul modello di Spoolman).

Una bobina si assegna a una stampante e a uno slot (AMS e slot per Bambu Lab, utensile per le altre). A ogni stampa
il consumo registrato per quello slot (misurato dalla stampante o stimato) viene scalato dalla bobina. Il calcolo è
ripetibile: se il consumo di una stampa viene ricalcolato (conferma dell'esito, correzioni), la bobina si riallinea.
"""
from __future__ import annotations

import time


def N_(s: str) -> str:
    """Segna una frase da tradurre (il messaggio arriva all'utente tramite l'API)."""
    return s

SCHEMA = """
CREATE TABLE IF NOT EXISTS mat_vendors (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, empty_spool_weight REAL, comment TEXT DEFAULT '', created_at REAL
);
CREATE TABLE IF NOT EXISTS mat_filaments (
    id INTEGER PRIMARY KEY, vendor_id INTEGER, name TEXT DEFAULT '', material TEXT NOT NULL, color_hex TEXT DEFAULT '',
    diameter REAL DEFAULT 1.75, density REAL DEFAULT 1.24, weight REAL DEFAULT 1000, spool_weight REAL,
    price REAL, article_number TEXT DEFAULT '', nozzle_temp INTEGER, bed_temp INTEGER, comment TEXT DEFAULT '',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS mat_spools (
    id INTEGER PRIMARY KEY, filament_id INTEGER NOT NULL, initial_weight REAL, used_weight REAL DEFAULT 0,
    spool_weight REAL, price REAL, location TEXT DEFAULT '', lot_nr TEXT DEFAULT '', comment TEXT DEFAULT '',
    archived INTEGER DEFAULT 0, created_at REAL, first_used REAL, last_used REAL,
    printer_sn TEXT, ams_id INTEGER, slot_id INTEGER, slot_label TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mat_usage (
    id INTEGER PRIMARY KEY, spool_id INTEGER, job_id INTEGER, grams REAL, ts REAL, note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS mat_usage_job ON mat_usage(job_id);
CREATE INDEX IF NOT EXISTS mat_usage_spool ON mat_usage(spool_id);
"""

# Densità tipiche (g/cm³) proposte quando si crea un filamento
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PA": 1.14, "PA-CF": 1.20, "PC": 1.20,
           "PP": 0.90, "PVA": 1.23, "HIPS": 1.04, "PEEK": 1.30, "PEKK": 1.30, "PEI": 1.27, "PLA-CF": 1.29,
           "PETG-CF": 1.30, "PPS": 1.35}


def init(db):
    db._conn.executescript(SCHEMA)


def _f(v, default=None):
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- elenco
def library(db) -> dict:
    vendors = db.q("SELECT * FROM mat_vendors ORDER BY name COLLATE NOCASE")
    filaments = db.q("""SELECT f.*, v.name AS vendor FROM mat_filaments f LEFT JOIN mat_vendors v ON v.id=f.vendor_id
                        ORDER BY v.name COLLATE NOCASE, f.material, f.name COLLATE NOCASE""")
    spools = db.q("""SELECT s.*, f.material, f.name AS filament_name, f.color_hex, f.diameter, f.weight AS net_weight,
                            v.name AS vendor, (SELECT COUNT(*) FROM mat_usage u WHERE u.spool_id=s.id) AS uses
                     FROM mat_spools s JOIN mat_filaments f ON f.id=s.filament_id LEFT JOIN mat_vendors v ON v.id=f.vendor_id
                     ORDER BY s.archived, s.id DESC""")
    for s in spools:
        init_w = s["initial_weight"] if s["initial_weight"] is not None else (s["net_weight"] or 0)
        s["initial_weight"] = init_w
        s["remaining"] = round(max(0.0, init_w - (s["used_weight"] or 0)), 1)
        s["pct"] = round(100 * s["remaining"] / init_w) if init_w else None
    active = [s for s in spools if not s["archived"]]
    return {"vendors": vendors, "filaments": filaments, "spools": spools,
            "summary": {"spools": len(active), "grams": round(sum(s["remaining"] for s in active)),
                        "low": sum(1 for s in active if s["pct"] is not None and s["pct"] < 15),
                        "assigned": sum(1 for s in active if s["printer_sn"])},
            "densities": DENSITY}


def assigned(db) -> dict[str, list[dict]]:
    """Bobine assegnate per stampante (per le schede delle stampanti)."""
    out: dict[str, list[dict]] = {}
    for s in library(db)["spools"]:
        if s["printer_sn"] and not s["archived"]:
            out.setdefault(s["printer_sn"], []).append(
                {"id": s["id"], "ams_id": s["ams_id"], "slot_id": s["slot_id"], "slot_label": s["slot_label"],
                 "material": s["material"], "name": s["filament_name"], "vendor": s["vendor"], "color": s["color_hex"],
                 "remaining": s["remaining"], "pct": s["pct"]})
    return out


# ---------------------------------------------------------------------- produttori, filamenti, bobine
def save_vendor(db, body: dict, vid: int | None = None) -> int:
    name = str(body.get("name") or "").strip()[:64]
    if not name:
        raise ValueError(N_("Dai un nome al produttore"))
    vals = (name, _f(body.get("empty_spool_weight")), str(body.get("comment") or "")[:1024])
    if vid:
        db.x("UPDATE mat_vendors SET name=?, empty_spool_weight=?, comment=? WHERE id=?", (*vals, vid))
        return vid
    return db.x("INSERT INTO mat_vendors(name, empty_spool_weight, comment, created_at) VALUES(?,?,?,?)", (*vals, time.time()))


def save_filament(db, body: dict, fid: int | None = None) -> int:
    material = str(body.get("material") or "").strip().upper()[:32]
    if not material:
        raise ValueError(N_("Indica il materiale (es. PLA, PETG)"))
    color = str(body.get("color_hex") or "").strip().upper()
    if color and not color.startswith("#"):
        color = "#" + color
    vals = (_i(body.get("vendor_id")), str(body.get("name") or "").strip()[:64], material, color[:9],
            _f(body.get("diameter"), 1.75), _f(body.get("density"), DENSITY.get(material, 1.24)),
            _f(body.get("weight"), 1000), _f(body.get("spool_weight")), _f(body.get("price")),
            str(body.get("article_number") or "")[:64], _i(body.get("nozzle_temp")), _i(body.get("bed_temp")),
            str(body.get("comment") or "")[:1024])
    cols = ("vendor_id, name, material, color_hex, diameter, density, weight, spool_weight, price, article_number, "
            "nozzle_temp, bed_temp, comment")
    if fid:
        db.x(f"UPDATE mat_filaments SET {', '.join(c.strip() + '=?' for c in cols.split(','))} WHERE id=?", (*vals, fid))
        return fid
    return db.x(f"INSERT INTO mat_filaments({cols}, created_at) VALUES({','.join('?' * 14)})", (*vals, time.time()))


def save_spool(db, body: dict, sid: int | None = None) -> int:
    fid = _i(body.get("filament_id"))
    if not fid or not db.one("SELECT 1 FROM mat_filaments WHERE id=?", (fid,)):
        raise ValueError(N_("Scegli il filamento della bobina"))
    vals = (fid, _f(body.get("initial_weight")), _f(body.get("spool_weight")), _f(body.get("price")),
            str(body.get("location") or "")[:64], str(body.get("lot_nr") or "")[:64], str(body.get("comment") or "")[:1024])
    if sid:
        db.x("UPDATE mat_spools SET filament_id=?, initial_weight=?, spool_weight=?, price=?, location=?, lot_nr=?, comment=? "
             "WHERE id=?", (*vals, sid))
        return sid
    return db.x("INSERT INTO mat_spools(filament_id, initial_weight, spool_weight, price, location, lot_nr, comment, "
                "used_weight, created_at) VALUES(?,?,?,?,?,?,?,0,?)", (*vals, time.time()))


def assign(db, sid: int, printer_sn: str | None, ams_id=None, slot_id=None, label: str = ""):
    """Assegna la bobina a uno slot di una stampante (la bobina che c'era in quello slot viene tolta)."""
    if printer_sn:
        db.x("UPDATE mat_spools SET printer_sn=NULL, ams_id=NULL, slot_id=NULL, slot_label='' WHERE printer_sn=? "
             "AND ams_id IS ? AND slot_id IS ? AND id<>?", (printer_sn, _i(ams_id), _i(slot_id), sid))
        db.x("UPDATE mat_spools SET printer_sn=?, ams_id=?, slot_id=?, slot_label=? WHERE id=?",
             (printer_sn, _i(ams_id), _i(slot_id), str(label or "")[:32], sid))
    else:
        db.x("UPDATE mat_spools SET printer_sn=NULL, ams_id=NULL, slot_id=NULL, slot_label='' WHERE id=?", (sid,))


def weigh(db, sid: int, gross: float | None = None, remaining: float | None = None):
    """Correzione del residuo: dal peso lordo pesato (bobina compresa) oppure dal residuo netto."""
    s = db.one("""SELECT s.*, f.weight AS net_weight, f.spool_weight AS f_spool, v.empty_spool_weight AS v_spool
                  FROM mat_spools s JOIN mat_filaments f ON f.id=s.filament_id LEFT JOIN mat_vendors v ON v.id=f.vendor_id
                  WHERE s.id=?""", (sid,))
    if not s:
        raise ValueError(N_("Bobina non trovata"))
    init_w = s["initial_weight"] if s["initial_weight"] is not None else (s["net_weight"] or 0)
    if gross is not None:
        tare = s["spool_weight"] if s["spool_weight"] is not None else (s["f_spool"] if s["f_spool"] is not None else s["v_spool"])
        if tare is None:
            raise ValueError(N_("Peso della bobina vuota sconosciuto: indicalo nella bobina, nel filamento o nel produttore"))
        remaining = max(0.0, gross - tare)
    if remaining is None:
        raise ValueError(N_("Indica il peso"))
    db.x("UPDATE mat_spools SET initial_weight=?, used_weight=? WHERE id=?", (init_w, round(max(0.0, init_w - remaining), 1), sid))
    db.x("INSERT INTO mat_usage(spool_id, job_id, grams, ts, note) VALUES(?,NULL,0,?,'weigh')", (sid, time.time()))


# ---------------------------------------------------------------------- consumo delle stampe
def _spool_for(spools: list[dict], row: dict) -> dict | None:
    """Bobina da cui scalare una riga di consumo: quella assegnata allo stesso slot. Solo se il consumo non indica lo
    slot: l'unica bobina della stampante, altrimenti quella dello stesso materiale e colore."""
    exact = [s for s in spools if s["ams_id"] == row["ams_id"] and s["slot_id"] == row["slot_id"]]
    if exact:
        return exact[0]
    if row["ams_id"] is not None or (row["slot_id"] is not None and any(s["slot_id"] is not None for s in spools)):
        return None          # il consumo indica uno slot preciso, diverso da quelli con una bobina assegnata
    if len(spools) == 1:
        return spools[0]
    same = [s for s in spools if (s["material"] or "").upper() == (row["type"] or "").upper()
            and (s["color_hex"] or "").upper()[:7] == (row["color"] or "").upper()[:7]]
    return same[0] if len(same) == 1 else None


def sync_job(db, job_id: int):
    """Riallinea le bobine al consumo registrato per una stampa: annulla quanto scalato in precedenza per quella stampa
    e scala di nuovo il consumo attuale (zero se la stampa è stata eliminata o non ha consumo)."""
    for u in db.q("SELECT * FROM mat_usage WHERE job_id=?", (job_id,)):
        db.x("UPDATE mat_spools SET used_weight=MAX(0, COALESCE(used_weight,0)-?) WHERE id=?", (u["grams"], u["spool_id"]))
    db.x("DELETE FROM mat_usage WHERE job_id=?", (job_id,))
    job = db.one("SELECT id, printer_sn, status, ended_at, started_at FROM jobs WHERE id=?", (job_id,))
    if not job or not job["printer_sn"]:
        return
    spools = db.q("""SELECT s.id, s.ams_id, s.slot_id, f.material, f.color_hex FROM mat_spools s
                     JOIN mat_filaments f ON f.id=s.filament_id WHERE s.printer_sn=? AND s.archived=0""", (job["printer_sn"],))
    if not spools:
        return
    ts = job["ended_at"] or job["started_at"] or time.time()
    for row in db.q("SELECT type, color, grams, ams_id, slot_id FROM job_filaments WHERE job_id=?", (job_id,)):
        if not row["grams"]:
            continue
        s = _spool_for(spools, row)
        if not s:
            continue
        db.x("UPDATE mat_spools SET used_weight=COALESCE(used_weight,0)+?, last_used=?, first_used=COALESCE(first_used, ?) "
             "WHERE id=?", (row["grams"], ts, job["started_at"] or ts, s["id"]))
        db.x("INSERT INTO mat_usage(spool_id, job_id, grams, ts) VALUES(?,?,?,?)", (s["id"], job_id, row["grams"], ts))


def usage(db, sid: int, limit: int = 50) -> list[dict]:
    return db.q("""SELECT u.ts, u.grams, u.note, j.task_name, j.printer_name, j.status FROM mat_usage u
                   LEFT JOIN jobs j ON j.id=u.job_id WHERE u.spool_id=? ORDER BY u.ts DESC LIMIT ?""", (sid, limit))
