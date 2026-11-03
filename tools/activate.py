#!/usr/bin/env python3
"""Attivazione del Fleet Hub (Layer 2, sezione 2.2 del PDF) usando config.toml.

  python -m tools.activate             # attiva usando hub.user_name / hub.password come account API
  python -m tools.activate --info      # mostra solo lo stato del Hub
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_config  # noqa: E402
from app.hub_client import FleetHubClient, HubError, activate_cloud  # noqa: E402


def check_password(pwd: str) -> str | None:
    if not 8 <= len(pwd.encode()) <= 64:
        return "la password deve avere da 8 a 64 byte"
    for rx, what in ((r"[A-Z]", "una maiuscola"), (r"[a-z]", "una minuscola"), (r"\d", "un numero"), (r"[^A-Za-z0-9]", "un carattere speciale")):
        if not re.search(rx, pwd):
            return f"la password deve contenere {what}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--login", action="store_true", help="prova un singolo login con le credenziali di config.toml")
    ap.add_argument("--config")
    a = ap.parse_args()
    cfg = load_config(a.config)
    hub = FleetHubClient(cfg)
    info = hub.captain()
    print(f"Hub {info.get('hub_sn')} firmware {info.get('version')} – stato: {info.get('activate_state')}")
    if a.info:
        return
    if a.login:
        left, reason = hub.login_blocked()
        try:
            hub._ensure_token()
            print(f"Login riuscito (password_hash_mode = {cfg.hub.password_hash_mode}).")
        except HubError as e:
            print(f"Login non riuscito: {e} (codice {e.code})")
            if e.code == 1026:
                print("Il Hub è in blocco: non fare altri tentativi per almeno 10 minuti.")
        return
    if info.get("activate_state") == "Activate":
        print("Il Hub è già attivato. Per riattivarlo serve un ripristino di fabbrica.")
        return
    err = check_password(cfg.hub.password)
    if err:
        sys.exit(f"Errore: {err} (hub.password in config.toml)")
    key = cfg.cloud.activation_key
    if not key.startswith("fh_sk_") or "..." in key or "REPLACE" in key:
        sys.exit("Errore: imposta cloud.activation_key in config.toml (Developer Center → Generate Activation Key)")
    print("1/3 Richiedo il ticket di attivazione al Hub…")
    ticket = hub.activation_ticket()
    print("2/3 Ottengo il codice di attivazione dal cloud Bambu…")
    code = activate_cloud(cfg.cloud.domain, cfg.cloud.activation_key, ticket)
    print("3/3 Installo il codice sul Hub e creo l'account API locale…")
    try:
        hub.install_activation_code(code, cfg.hub.user_name, cfg.hub.password)
    except HubError as e:
        if e.code == 1043:
            sys.exit("Ticket scaduto: rilancia il comando")
        raise
    print("Hub attivato. Ora puoi avviare FleetBuddy.")


if __name__ == "__main__":
    try:
        main()
    except HubError as e:
        sys.exit(f"Errore dal Hub: {e} (codice {e.code})")
