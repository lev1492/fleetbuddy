"""Persistenza SQLite (archivio file, storico stampe, coda, cache HMS, impostazioni)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY, md5 TEXT UNIQUE, filename TEXT, path TEXT, size INTEGER,
    uploaded_at REAL, meta TEXT, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, printer_sn TEXT, printer_name TEXT, task_name TEXT, task_id TEXT,
    file_id INTEGER, plate INTEGER, started_at REAL, ended_at REAL, status TEXT,
    progress INTEGER DEFAULT 0, planned_g REAL DEFAULT 0, filament_g REAL DEFAULT 0,
    error TEXT DEFAULT '', snapshot TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_started ON jobs(started_at);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY, file_id INTEGER, plate INTEGER, printer_sn TEXT,
    mapping TEXT, options TEXT, status TEXT DEFAULT 'queued', position REAL,
    created_at REAL, started_at REAL, message TEXT DEFAULT '', copies INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hms_cache (key TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, display_name TEXT DEFAULT '',
    pw_hash TEXT, role TEXT DEFAULT 'operator', active INTEGER DEFAULT 1, created_at REAL, last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER, expires REAL, created_at REAL, ip TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS printer_groups (id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, color TEXT DEFAULT '#0E7C6B');
CREATE TABLE IF NOT EXISTS group_printers (group_id INTEGER, printer_sn TEXT, PRIMARY KEY (group_id, printer_sn));
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, description TEXT DEFAULT '',
    color TEXT DEFAULT '#2B63C6', created_at REAL, created_by INTEGER
);
-- consumo di filamento per stampa (una riga per filamento usato)
CREATE TABLE IF NOT EXISTS job_filaments (
    id INTEGER PRIMARY KEY, job_id INTEGER, type TEXT, brand TEXT DEFAULT '', color TEXT, tray_info_idx TEXT DEFAULT '',
    grams REAL DEFAULT 0, meters REAL DEFAULT 0, ams_id INTEGER, slot_id INTEGER
);
CREATE INDEX IF NOT EXISTS job_filaments_job ON job_filaments(job_id);
-- stampe appena inviate da FleetBuddy, in attesa che il Hub le segnali come avviate (sopravvive ai riavvii)
CREATE TABLE IF NOT EXISTS pending_prints (printer_sn TEXT PRIMARY KEY, data TEXT, at REAL);
-- permessi: kind = 'printer' (ref = seriale), 'group' (ref = id gruppo), 'project' (ref = id progetto)
CREATE TABLE IF NOT EXISTS access (user_id INTEGER, kind TEXT, ref TEXT, PRIMARY KEY (user_id, kind, ref));
-- stampanti collegate direttamente, senza Fleet Hub (es. Duet / RepRapFirmware)
CREATE TABLE IF NOT EXISTS direct_printers (
    id INTEGER PRIMARY KEY, kind TEXT DEFAULT 'duet', name TEXT, host TEXT, password TEXT DEFAULT '',
    snapshot_url TEXT DEFAULT '', enabled INTEGER DEFAULT 1, created_at REAL
);
"""

# colonne aggiunte nelle versioni successive (migrazione automatica dei database esistenti)
MIGRATIONS = [
    ("files", "project_id", "INTEGER"),
    ("files", "uploaded_by", "INTEGER"),
    ("queue", "group_id", "INTEGER"),
    ("queue", "created_by", "INTEGER"),
    ("jobs", "started_by", "INTEGER"),
    ("users", "language", "TEXT"),
    ("users", "first_name", "TEXT DEFAULT ''"),
    ("jobs", "filaments", "TEXT"),
    ("users", "last_name", "TEXT DEFAULT ''"),
    ("jobs", "start_trays", "TEXT"),
    ("users", "must_change_pw", "INTEGER DEFAULT 0"),
    ("job_filaments", "source", "TEXT DEFAULT 'slicer'"),
]


class DB:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")  # meno scritture sulla microSD
            for table, col, typ in MIGRATIONS:
                cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                    if (table, col) == ("users", "last_name"):
                        # il vecchio "nome visualizzato" viene diviso in nome (prima parola) e cognome (il resto)
                        for uid, dn in self._conn.execute("SELECT id, COALESCE(display_name,'') FROM users").fetchall():
                            first, _, last = dn.strip().partition(" ")
                            self._conn.execute("UPDATE users SET first_name=?, last_name=? WHERE id=?",
                                               (first, last.strip(), uid))
            self._conn.commit()

    def q(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql: str, args=()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value):
        self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    # ---------------------------------------------------------------- files
    def file(self, fid: int) -> dict | None:
        r = self.one("SELECT * FROM files WHERE id=?", (fid,))
        if r:
            r["meta"] = json.loads(r["meta"] or "{}")
        return r

    def files(self) -> list[dict]:
        rows = self.q("""SELECT f.*, (SELECT COUNT(*) FROM jobs j WHERE j.file_id=f.id) AS prints,
                         (SELECT COUNT(*) FROM jobs j WHERE j.file_id=f.id AND j.status='finished') AS ok
                         FROM files f ORDER BY uploaded_at DESC""")
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    # ---------------------------------------------------------------- jobs
    def open_jobs(self) -> dict[str, dict]:
        return {r["printer_sn"]: r for r in self.q("SELECT * FROM jobs WHERE status='running'")}

    def start_job(self, **kw) -> int:
        kw.setdefault("started_at", time.time())
        kw["status"] = "running"
        cols = ",".join(kw)
        return self.x(f"INSERT INTO jobs({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))

    def update_job(self, jid: int, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE jobs SET {sets} WHERE id=?", (*kw.values(), jid))
