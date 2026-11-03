#!/usr/bin/env python3
"""Gestione utenti di FleetBuddy da riga di comando (utile se si dimentica la password admin).

  python -m tools.users list
  python -m tools.users reset-password <utente>        # chiede la nuova password
  python -m tools.users create <utente> --role admin   # chiede la password
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.auth import ROLES, Auth, hash_password, password_problem  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import DB  # noqa: E402


def ask_password() -> str:
    p1 = getpass.getpass("Nuova password: ")
    if getpass.getpass("Ripeti la password: ") != p1:
        sys.exit("Le password non coincidono")
    problem = password_problem(p1)
    if problem:
        sys.exit(problem)
    return p1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("reset-password"); r.add_argument("username")
    c = sub.add_parser("create"); c.add_argument("username"); c.add_argument("--role", choices=ROLES, default="admin")
    a = ap.parse_args()
    cfg = load_config()
    db = DB(cfg.data_path / "fleetbuddy.db")
    if a.cmd == "list":
        for u in db.q("SELECT username, role, active FROM users ORDER BY username"):
            print(f"{u['username']:20} {u['role']:9} {'attivo' if u['active'] else 'disattivato'}")
    elif a.cmd == "reset-password":
        u = db.one("SELECT id FROM users WHERE username=?", (a.username,))
        if not u:
            sys.exit("Utente inesistente")
        db.x("UPDATE users SET pw_hash=?, active=1 WHERE id=?", (hash_password(ask_password()), u["id"]))
        db.x("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        print("Password aggiornata.")
    elif a.cmd == "create":
        try:
            Auth(db).create_user(a.username, ask_password(), a.role)
        except ValueError as e:
            sys.exit(str(e))
        print("Utente creato.")


if __name__ == "__main__":
    main()
