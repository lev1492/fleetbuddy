"""Utenti, sessioni e permessi di Fabbrix Farm Manager.

Ruoli:
  admin     tutto, compresi Hub, utenti, gruppi, progetti e permessi
  operator  vede e comanda le stampanti consentite, stampa i file dei progetti consentiti
  viewer    solo visualizzazione di stampanti e progetti consentiti
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field

from .db import DB
from .i18n import _

ROLES = ("admin", "manager", "operator", "viewer")    # ruoli predefiniti (l'amministratore può crearne altri)

def N_(s: str) -> str:
    """Segna una frase da tradurre."""
    return s


# Permessi che l'amministratore combina nei ruoli (chiave → descrizione nel catalogo traduzioni)
PERMISSIONS = {
    "all": N_("Vede tutte le stampanti e tutti i file"),
    "control": N_("Comanda le stampanti (pausa, stop, temperature, velocità, filamenti)"),
    "print": N_("Avvia stampe e gestisce la coda"),
    "files": N_("Carica e organizza i file e le cartelle dell'archivio"),
    "production": N_("Gestisce la produzione (ordini, priorità, avvio dei pezzi)"),
    "confirm": N_("Conferma l'esito delle stampe da confermare"),
    "history": N_("Corregge lo storico (elimina voci)"),
    "stats": N_("Vede le statistiche e le esporta"),
    "users": N_("Gestisce gli utenti dei ruoli indicati"),
}
OPERATIVE = ["control", "print", "files", "production", "confirm", "stats"]
BUILTIN_ROLES = {
    # chiave: (nome, permessi, ruoli assegnabili da chi ha "users")
    "admin": (N_("Amministratore"), list(PERMISSIONS), []),
    "manager": (N_("Manager"), ["all", *OPERATIVE, "history", "users"], ["operator", "viewer"]),
    "operator": (N_("Operatore"), OPERATIVE, []),
    "viewer": (N_("Visitatore"), ["stats"], []),
}


def ensure_roles(db):
    """Tabella dei ruoli con quelli predefiniti (al primo avvio o dopo l'aggiornamento)."""
    db._conn.executescript("""CREATE TABLE IF NOT EXISTS roles (
        key TEXT PRIMARY KEY, name TEXT, perms TEXT, assign TEXT, builtin INTEGER DEFAULT 0, position INTEGER DEFAULT 0)""")
    for i, (k, (name, perms, assign)) in enumerate(BUILTIN_ROLES.items()):
        if not db.one("SELECT 1 FROM roles WHERE key=?", (k,)):
            db.x("INSERT INTO roles(key, name, perms, assign, builtin, position) VALUES(?,?,?,?,1,?)",
                 (k, name, json.dumps(perms), json.dumps(assign), i))
    # l'amministratore ha sempre tutti i permessi, anche quelli aggiunti nelle versioni successive
    db.x("UPDATE roles SET perms=? WHERE key='admin'", (json.dumps(list(PERMISSIONS)),))


def role_keys(db) -> list[str]:
    return [r["key"] for r in db.q("SELECT key FROM roles ORDER BY position, key")]
SESSION_DAYS = 30
PBKDF2_ROUNDS = 120_000          # ~0,3 s su un Pi 3: sufficiente e non blocca l'interfaccia


def hash_password(pwd: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(pwd: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


def password_problem(pwd: str) -> str | None:
    if len(pwd) < 8:
        return _("La password deve avere almeno 8 caratteri")
    if not (re.search(r"[A-Za-z]", pwd) and re.search(r"\d", pwd)):
        return _("La password deve contenere lettere e numeri")
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class User:
    id: int
    username: str
    role: str
    language: str | None = None
    theme: str = ""                                   # tema scelto dall'utente ('' = predefinito)
    printers: set[str] = field(default_factory=set)   # seriali consentiti (non usato per admin)
    projects: set[int] = field(default_factory=set)   # progetti consentiti (non usato per admin)
    must_change_pw: bool = False                      # password assegnata dall'admin: proporre di cambiarla
    perms: set[str] = field(default_factory=set)      # permessi del ruolo
    assign: set[str] = field(default_factory=set)     # ruoli che può assegnare (con il permesso "users")
    role_name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can(self, perm: str) -> bool:
        return self.is_admin or perm in self.perms

    @property
    def can_operate(self) -> bool:                    # compatibilità: comandi delle stampanti
        return self.can("control")

    @property
    def sees_all(self) -> bool:
        return self.is_admin or "all" in self.perms

    def sees_printer(self, sn: str | None) -> bool:
        return self.sees_all or (sn is not None and sn in self.printers)

    def sees_project(self, pid: int | None) -> bool:
        # i file senza progetto sono visibili solo a chi vede tutto
        return self.sees_all or (pid is not None and pid in self.projects)

    def can_assign(self, role: str) -> bool:
        """Può creare o modificare utenti di questo ruolo? (l'amministratore tutti, gli altri solo quelli indicati)"""
        return self.is_admin or (self.can("users") and role in self.assign)

    def public(self) -> dict:
        # nome e cognome restano nella sola gestione utenti
        return {"id": self.id, "username": self.username, "role": self.role, "language": self.language, "theme": self.theme,
                "must_change_pw": self.must_change_pw, "perms": sorted(PERMISSIONS if self.is_admin else self.perms),
                "assign": sorted(self.assign), "role_name": self.role_name}


class Auth:
    def __init__(self, db: DB):
        self.db = db
        self._fails: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ utenti
    def has_users(self) -> bool:
        return bool(self.db.one("SELECT 1 FROM users LIMIT 1"))

    def create_user(self, username: str, password: str, role: str = "operator", language: str | None = None,
                    first_name: str = "", last_name: str = "") -> int:
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9._@-]{2,32}", username):
            raise ValueError(_("Nome utente: 2–32 caratteri tra lettere, numeri e . _ @ -"))
        if role not in role_keys(self.db):
            raise ValueError(_("Ruolo non valido"))
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        if self.db.one("SELECT 1 FROM users WHERE username=?", (username,)):
            raise ValueError(_("Nome utente già esistente"))
        return self.db.x("INSERT INTO users(username, first_name, last_name, pw_hash, role, created_at, language) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (username, first_name.strip()[:64], last_name.strip()[:64], hash_password(password), role,
                          time.time(), language))

    def load(self, uid: int, printer_groups: dict[int, set[str]] | None = None) -> User | None:
        r = self.db.one("SELECT * FROM users WHERE id=? AND active=1", (uid,))
        if not r:
            return None
        u = User(r["id"], r["username"], r["role"], r.get("language"), must_change_pw=bool(r.get("must_change_pw")),
                 theme=r.get("theme") or "")
        role = self.db.one("SELECT * FROM roles WHERE key=?", (r["role"],))
        if role:
            u.perms, u.assign, u.role_name = set(json.loads(role["perms"] or "[]")), set(json.loads(role["assign"] or "[]")), role["name"]
        if not u.is_admin:
            acc = self.db.q("SELECT kind, ref FROM access WHERE user_id=?", (uid,))
            groups = printer_groups if printer_groups is not None else self.group_members()
            for a in acc:
                if a["kind"] == "printer":
                    u.printers.add(a["ref"])
                elif a["kind"] == "group":
                    u.printers |= groups.get(int(a["ref"]), set())
                elif a["kind"] == "project":
                    u.projects.add(int(a["ref"]))
            if u.projects:
                children: dict[int, list[int]] = {}
                for r in self.db.q("SELECT id, parent_id FROM projects WHERE parent_id IS NOT NULL"):
                    children.setdefault(r["parent_id"], []).append(r["id"])
                todo = list(u.projects)
                while todo:                                     # sottocartelle a qualsiasi profondità
                    for c in children.get(todo.pop(), []):
                        if c not in u.projects:
                            u.projects.add(c)
                            todo.append(c)
        return u

    def group_members(self) -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        for r in self.db.q("SELECT group_id, printer_sn FROM group_printers"):
            out.setdefault(r["group_id"], set()).add(r["printer_sn"])
        return out

    def admins_left(self, excluding: int) -> int:
        return self.db.one("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND id<>?", (excluding,))["n"]

    # ------------------------------------------------------------------ login
    def login_blocked(self, ip: str) -> bool:
        recent = [t for t in self._fails.get(ip, []) if time.time() - t < 300]
        self._fails[ip] = recent
        return len(recent) >= 5

    def login(self, username: str, password: str, ip: str) -> str | None:
        r = self.db.one("SELECT * FROM users WHERE username=? AND active=1", (username.strip(),))
        if not r or not verify_password(password, r["pw_hash"]):
            self._fails.setdefault(ip, []).append(time.time())
            return None
        self._fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.x("INSERT INTO sessions(token_hash, user_id, expires, created_at, ip) VALUES(?,?,?,?,?)",
                  (_token_hash(token), r["id"], now + SESSION_DAYS * 86400, now, ip))
        self.db.x("UPDATE users SET last_login=? WHERE id=?", (now, r["id"]))
        self.db.x("DELETE FROM sessions WHERE expires<?", (now,))
        return token

    def session_user_id(self, token: str | None) -> int | None:
        if not token:
            return None
        r = self.db.one("SELECT user_id, expires FROM sessions WHERE token_hash=?", (_token_hash(token),))
        if not r or r["expires"] < time.time():
            return None
        return r["user_id"]

    def logout(self, token: str | None):
        if token:
            self.db.x("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    def drop_sessions(self, uid: int):
        self.db.x("DELETE FROM sessions WHERE user_id=?", (uid,))
