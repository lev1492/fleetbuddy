"""Client per la Bambu Fleet Hub HTTP API v1.0.0.

Basato sugli script demo del produttore (base_lib/hub_https_lib.py, login.py):
  - mTLS con certificato client firmato dal proprio account Bambu
  - SNI forzato a "fleet_v1.bambulab.com"
  - login a due passi (ticket -> token JWT) con hash challenge/salt
Il client è sincrono (requests); il resto dell'app lo usa tramite asyncio.to_thread.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter

from .config import Config
from .i18n import tr

log = logging.getLogger("fleetbuddy.hub")


class HubError(Exception):
    """Errore del Hub o della connessione.

    `key` è una frase italiana del catalogo traduzioni (con `params`); in alternativa `message`
    è il testo restituito dal Hub, a cui si aggiunge la spiegazione del codice nella lingua richiesta.
    I parametri il cui nome finisce in `_key` sono a loro volta frasi da tradurre.
    """

    def __init__(self, message: str = "", code: int | None = None, status: int | None = None,
                 key: str | None = None, params: dict | None = None):
        self.raw, self.code, self.status = message, code, status
        self.key, self.params = key, params or {}
        super().__init__(self.localized("it"))

    def localized(self, lang: str | None) -> str:
        if self.key:
            params = {k: (tr(lang, v) if k.endswith("_key") else v) for k, v in self.params.items()}
            return tr(lang, self.key, **params)
        hint = ERROR_HINTS.get(self.code) if isinstance(self.code, int) else None
        if not self.raw:
            return tr(lang, hint) if hint else tr(lang, "Errore del Hub")
        return f"{self.raw} ({tr(lang, hint)})" if hint else self.raw

    def to_dict(self):
        return {"error": str(self), "code": self.code, "status": self.status}


# Messaggi in italiano per i codici più frequenti (sezione 7 del PDF)
ERROR_HINTS = {
    3: "Hub non attivato",
    6: "Token non valido",
    7: "Token scaduto",
    1004: "Il file deve essere un .gcode.3mf a piatto singolo",
    1023: "Nome utente o password errati",
    1026: "Troppi login ravvicinati, riprova tra poco",
    1035: "Stampante offline",
    1043: "Ticket scaduto",
    1051: "Stampante occupata (in stampa, aggiornamento, offline o piatto da liberare)",
    1052: "Scheda SD mancante (serie P/A)",
    1053: "Mappatura filamenti non valida (slot vuoto o AMS inesistente)",
    1065: "Spazio insufficiente sul Hub",
}


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def password_hash(password: str, salt: str, challenge: str, mode: str) -> str:
    if mode == "doc":
        # PDF: hex(sha256(hex(sha256(hex(sha256(pwd)) + salt)) + challenge))
        return _sha256_hex(_sha256_hex(_sha256_hex(password) + salt) + challenge)
    # demo login.py: hex(sha256(hex(sha256(pwd + salt)) + challenge))
    return _sha256_hex(_sha256_hex(password + salt) + challenge)


class _SSLAdapter(HTTPAdapter):
    def __init__(self, ctx: ssl.SSLContext, sni: str, **kw):
        self._ctx = ctx
        self._sni = sni
        super().__init__(**kw)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._ctx
        kwargs["server_hostname"] = self._sni
        return super().init_poolmanager(*args, **kwargs)


class FleetHubClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        h = cfg.hub
        self.base = f"https://{h.host}:{h.port}"
        self._token: str | None = None
        self._lock = threading.Lock()
        self._last_login = 0.0
        self._login_block_until = 0.0
        self._login_block_reason = ""
        self._login_block_code: int | None = None
        self._mode_fallback_tried = False
        self._session = self._make_session()

    # ------------------------------------------------------------------ infra
    def _make_session(self) -> requests.Session:
        h = self.cfg.hub
        key = self.cfg.path(h.client_key)
        ctx = ssl.create_default_context(cafile=str(self.cfg.path(h.server_ca)))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # minimo richiesto dal Hub (Security White Paper 5.1)
        ctx.load_cert_chain(str(self.cfg.path(h.client_cert)), str(key))
        try:
            if key.stat().st_mode & 0o077:
                log.warning("La chiave privata %s è leggibile da altri utenti: esegui chmod 600", key)
        except OSError:
            pass
        s = requests.Session()
        s.mount("https://", _SSLAdapter(ctx, h.sni_hostname, pool_connections=4, pool_maxsize=8))
        s.headers.update({"Accept": "application/json", "Accept-Language": "en-US"})
        return s

    def _raw(self, method: str, path: str, *, auth=True, timeout=30, stream=False, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        if auth:
            headers["Authorization"] = f"Bearer {self._ensure_token()}"
        url = self.base + path
        try:
            return self._session.request(method, url, headers=headers, timeout=timeout, stream=stream, **kw)
        except requests.exceptions.SSLError as e:
            raise HubError(key="Errore TLS/mTLS verso il Hub: {e}", params={"e": e}) from e
        except requests.exceptions.RequestException as e:
            raise HubError(key="Hub non raggiungibile: {e}", params={"e": e}) from e

    @staticmethod
    def _parse(resp: requests.Response) -> dict:
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {"raw": resp.text}
        if not isinstance(data, dict):
            data = {"data": data}
        code = data.get("code", 0)
        if resp.status_code >= 400 or (isinstance(code, int) and code != 0):
            msg = data.get("message") or ("" if code in ERROR_HINTS else resp.reason)
            raise HubError(msg, code=code, status=resp.status_code)
        return data

    def request(self, method: str, path: str, *, auth=True, retry=True, **kw) -> dict:
        resp = self._raw(method, path, auth=auth, **kw)
        # nuovo login solo se il token non è appena stato emesso (evita raffiche di login → errore 1026)
        if auth and retry and resp.status_code == 401 and time.time() - self._last_login > 60:
            self._token = None  # token scaduto -> nuovo login e un solo nuovo tentativo
            resp = self._raw(method, path, auth=auth, **kw)
        return self._parse(resp)

    # ------------------------------------------------------------------ login
    # Il Hub blocca i login per ~10 minuti dopo troppi tentativi (errore 1026), e ogni
    # tentativo durante il blocco lo prolunga. Per questo, dopo un login fallito,
    # Fabbrix Farm Manager smette di ritentare per un intervallo adeguato invece di riprovare a ogni polling.
    LOGIN_BACKOFF = {1026: 11 * 60, 1023: 15 * 60}

    def login_blocked(self) -> tuple[float, str]:
        """(secondi rimanenti, motivo) se i login sono sospesi, altrimenti (0, '')."""
        left = self._login_block_until - time.time()
        return (left, self._login_block_reason) if left > 0 else (0, "")

    def _ensure_token(self) -> str:
        with self._lock:
            if self._token:
                return self._token
            left, reason = self.login_blocked()
            if left > 0:
                raise HubError(code=self._login_block_code, key="{reason_key} – prossimo tentativo di login tra {n} min",
                               params={"reason_key": reason, "n": int(left // 60) + 1})
            wait = 3 - (time.time() - self._last_login)
            if wait > 0:
                time.sleep(wait)
            try:
                self._token = self._login()
            except HubError as e:
                self._block_login(e)
                raise
            return self._token

    def _block_login(self, e: HubError):
        secs = self.LOGIN_BACKOFF.get(e.code)
        if e.code == 1026:
            m = re.search(r"after\s+(\d+)\s*min", str(e))
            if m:
                secs = int(m.group(1)) * 60 + 60
            reason = "Il Hub ha bloccato temporaneamente i login (troppi tentativi)"
        elif e.code == 1023:
            reason = "Nome utente o password dell'account API errati: correggi config.toml e riavvia Fabbrix Farm Manager"
        else:
            secs, reason = 30, "Login non riuscito"
        self._login_block_until = time.time() + secs
        self._login_block_reason = reason
        self._login_block_code = e.code
        log.error("%s (codice %s). Nessun nuovo tentativo per %d min.", reason, e.code, secs // 60 or 1)

    def _login_once(self, mode: str) -> str:
        h = self.cfg.hub
        self._last_login = time.time()
        t = self.request("POST", "/v1/hub/login/local/tickets", auth=False, json={"user_name": h.user_name})
        tk = json.loads(base64.b64decode(t["ticket"]))
        r = self.request("POST", "/v1/hub/login", auth=False, json={
            "user_name": h.user_name,
            "password": password_hash(h.password, tk["salt"], tk["challenge"], mode),
            "ticket_id": t["ticket_id"],
        })
        return r["token"]

    def _login(self) -> str:
        h = self.cfg.hub
        try:
            token = self._login_once(h.password_hash_mode)
        except HubError as e:
            # Seconda formula provata una sola volta per avvio, e solo se la password è stata rifiutata.
            if e.code != 1023 or self._mode_fallback_tried:
                raise
            self._mode_fallback_tried = True
            other = "doc" if h.password_hash_mode == "demo" else "demo"
            time.sleep(3)
            token = self._login_once(other)
            log.warning("Login riuscito con password_hash_mode=%s: salvo l'impostazione in config.toml", other)
            h.password_hash_mode = other
            self._save_hash_mode(other)
        self._login_block_until = 0
        log.info("Login al Hub riuscito")
        return token

    def _save_hash_mode(self, mode: str):
        from .config import BASE_DIR
        import os
        path = Path(os.environ.get("FLEETBUDDY_CONFIG") or BASE_DIR / "config.toml")
        try:
            text = path.read_text(encoding="utf-8")
            new, n = re.subn(r'(?m)^password_hash_mode\s*=.*$', f'password_hash_mode = "{mode}"', text, count=1)
            if n:
                path.write_text(new, encoding="utf-8")
        except OSError as err:
            log.warning("Impossibile aggiornare config.toml: %s", err)

    def logout(self):
        self._token = None

    # ------------------------------------------------------------------ hub
    def captain(self) -> dict:
        return self.request("GET", "/v1/hub/captain", auth=False)

    def get_ntp(self) -> dict:
        return self.request("GET", "/v1/hub/captain/ntp")

    def set_ntp(self, body: dict) -> dict:
        return self.request("PUT", "/v1/hub/captain/ntp", json=body)

    # ------------------------------------------------------------------ activation (layer 2)
    def activation_ticket(self) -> str:
        return self.request("POST", "/v1/hub/activation/tickets", auth=False, json={})["ticket"]

    def install_activation_code(self, code: str, user_name: str, password: str) -> dict:
        return self.request("PUT", "/v1/hub/activation/tickets/current", auth=False,
                            json={"activation_code": code, "user_name": user_name, "password": password})

    # ------------------------------------------------------------------ discovery / bind
    def local_printers(self, only_can_bind=False) -> list[dict]:
        r = self.request("GET", f"/v1/hub/local_printers?only_can_bind={'true' if only_can_bind else 'false'}")
        return r.get("printers") or []

    def scan_printers(self, prefix: str, begin: int, end: int) -> list[dict]:
        r = self.request("GET", "/v1/hub/scan_printers", timeout=180,
                         params={"prefix": prefix, "begin": begin, "end": end})
        return r.get("printers") or []

    def bind(self, dev_id: str, dev_ip: str) -> dict:
        return self.request("PUT", "/v1/hub/bind", timeout=60,
                            json={"dev_id": dev_id, "dev_ip": dev_ip, "dev_info": None, "sig": ""})

    def unbind(self, dev_ids: Iterable[str]) -> dict:
        return self.request("DELETE", "/v1/hub/bind", json={"dev_ids": list(dev_ids)})

    # ------------------------------------------------------------------ devices
    def devices(self) -> list[dict]:
        return self.request("GET", "/v1/hub/devices").get("devices") or []

    def device(self, sn: str) -> dict:
        r = self.request("GET", f"/v1/hub/devices/{sn}")
        return r.get("device") or r

    def opt(self, sn: str, opt: str, extra: dict | None = None) -> dict:
        body = {"dev_id": sn, "opt": opt}
        if extra:
            body.update(extra)
        return self.request("POST", f"/v1/hub/devices/{sn}/opt", json=body)

    def set_name(self, sn: str, name: str) -> dict:
        return self.request("PUT", f"/v1/hub/devices/{sn}/name", json={"name": name})

    def snapshot(self, sn: str) -> bytes | None:
        resp = self._raw("GET", "/v1/hub/file/info", params={"file_path": f"liveview/{sn}/liveview.jpeg"},
                         timeout=20)
        if resp.status_code == 401 and time.time() - self._last_login > 60:
            self._token = None
            resp = self._raw("GET", "/v1/hub/file/info", params={"file_path": f"liveview/{sn}/liveview.jpeg"},
                             timeout=20)
        if resp.status_code != 200 or resp.headers.get("Content-Type", "").startswith("application/json"):
            return None
        return resp.content

    def hms_description(self, model: str, code: str, kind: str = "hms") -> str:
        path = "/v1/hub/hms" if kind == "hms" else "/v1/hub/device_error"
        r = self.request("GET", path, params={"dev_model": model, "error_code": code})
        return r.get("description", "")

    # ------------------------------------------------------------------ print
    def cached_files(self, num=100) -> list[dict]:
        r = self.request("GET", "/v1/hub/file/info", params={"is_encrypted": "False", "num": num})
        return r.get("file_infos") or []

    def print_upload(self, file_path: str, filename: str, print_cmd: dict, dev_sns: list[str]) -> dict:
        with open(file_path, "rb") as fh:
            files = [("file", (filename, fh, "application/octet-stream")),
                     ("print_cmd", (None, json.dumps(print_cmd)))]
            files += [("dev_sns", (None, sn)) for sn in dev_sns]
            return self.request("PUT", "/v1/hub/devices/print", files=files, timeout=600)

    def print_cached(self, file_hash: str, print_cmd: dict, dev_sns: list[str]) -> dict:
        files = [("print_cmd", (None, json.dumps(print_cmd), "application/json"))]
        files += [("dev_sns", (None, sn, "application/json")) for sn in dev_sns]
        files.append(("file_hash", (None, file_hash, "application/json")))
        return self.request("PUT", "/v1/hub/devices/print", files=files, timeout=120)

    def print_url(self, dev_sns: list[str], print_cmd: dict, url: str, resolve_result: str = "") -> dict:
        return self.request("PUT", "/v1/hub/devices/external_url_print", json={
            "dev_sns": dev_sns, "print_cmd": print_cmd, "url": url,
            "resolve_result": resolve_result, "is_enc_print": False})

    def get_external_ca(self) -> dict:
        return self.request("GET", "/v1/hub/external_ca")

    def set_external_ca(self, pem: str) -> dict:
        return self.request("PUT", "/v1/hub/external_ca", json={"ca_cert": pem})

    # ------------------------------------------------------------------ firmware
    def firmwares(self) -> list[dict]:
        return self.request("GET", "/v1/hub/file/firmwares").get("firmwares") or []

    def upload_firmware(self, file_path: str, name: str, md5: str) -> dict:
        with open(file_path, "rb") as fh:
            return self.request("POST", "/v1/hub/file/firmwares", timeout=900,
                                files={"firmware": (name, fh, "application/octet-stream")},
                                data={"name": name, "md5": md5})

    def delete_firmwares(self, names: list[str]) -> dict:
        return self.request("DELETE", "/v1/hub/file/firmwares", json={"firmwares": names})

    def upgrade(self, dev_ids: list[str], firmware_name: str) -> dict:
        return self.request("PUT", "/v1/hub/devices/firmware",
                            json={"dev_ids": dev_ids, "firmware_name": firmware_name})

    # ------------------------------------------------------------------ users
    def change_password(self, old: str, new: str) -> dict:
        return self.request("PATCH", "/v1/hub/users/current_user/passwd",
                            json={"old_password": old, "new_password": new})

    def add_web_user(self, username: str, password: str) -> dict:
        return self.request("POST", "/v1/hub/web/user", json={"username": username, "password": password})

    def delete_web_user(self) -> dict:
        return self.request("DELETE", "/v1/hub/web/user")


def activate_cloud(domain: str, activation_key: str, ticket: str) -> str:
    """Passo 3 dell'attivazione: scambia il ticket del Hub con un activation_code firmato."""
    r = requests.put(
        f"https://{domain}/v1/iot-service2/api/fleet_hub/activate",
        json={"activation_ticket": ticket},
        headers={"Authorization": f"Bearer {activation_key}", "Content-Type": "application/json",
                 "x-bbl-client-name": "fleetbuddy", "x-bbl-client-type": "hub_script",
                 "x-bbl-client-version": "01.01.01.01"},
        timeout=30)
    if r.status_code == 401:
        raise HubError("Activation Key non valida (rigenerala dal Developer Center)", status=401)
    r.raise_for_status()
    data = r.json()
    if not data.get("activation_code"):
        raise HubError(f"Risposta cloud senza activation_code: {data}")
    return data["activation_code"]
