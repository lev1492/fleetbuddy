"""Base comune dei connettori per le stampanti collegate direttamente (senza Fleet Hub).

Ogni connettore legge lo stato grezzo della stampante (model), lo trasforma nella stessa forma usata per le
Bambu (normalize) e, dove l'API lo consente, esegue comandi: pausa, ripresa, annullamento, avvio di un file,
temperature e velocità. Le capacità effettive sono dichiarate in `caps`, così l'interfaccia mostra solo
i comandi davvero disponibili.
"""
from __future__ import annotations

import time
import urllib.parse

import requests

from ..hub_client import HubError

TIMEOUT = 6


class DirectClient:
    kind = ""
    label = ""
    default_brand = ""
    default_port: int | None = None
    # print_options: opzioni proposte nel dialogo di stampa (es. "bed_leveling", "timelapse"); vuoto = nessuna
    caps = {"temperature": False, "speed": False, "filament": False, "bed_clean": False, "print_options": []}

    def __init__(self, row: dict):
        self.id = row["id"]
        self.sn = f"{self.kind}-{row['id']}"
        self.name = row.get("name") or self.sn
        host = (row.get("host") or "").strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        parts = urllib.parse.urlsplit(host)
        if self.default_port and not parts.port:
            host = f"{parts.scheme}://{parts.hostname}:{self.default_port}{parts.path}"
        self.base = host
        self.api_key = (row.get("api_key") or "").strip()
        self.username = (row.get("username") or "").strip()
        self.password = row.get("password") or ""
        self.extra = (row.get("extra") or "").strip()
        self.snapshot_url = (row.get("snapshot_url") or "").strip()
        self.brand = (row.get("brand") or "").strip() or self.default_brand
        self.printer_model = (row.get("printer_model") or "").strip()
        self.auto_snapshot_url = ""          # trovato dalla stampante stessa (es. OctoPrint, Moonraker)
        self._s = requests.Session()

    # ------------------------------------------------------------------ da implementare
    def model(self) -> dict:
        raise NotImplementedError

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        raise NotImplementedError

    def info(self, raw: dict) -> dict:
        """Riepilogo per la prova di connessione: scheda/software, versione, modalità, stato, utensili."""
        return {}

    def pause(self):
        raise HubError(key="Comando non disponibile per questa stampante")

    def resume(self):
        raise HubError(key="Comando non disponibile per questa stampante")

    def stop(self):
        raise HubError(key="Comando non disponibile per questa stampante")

    def start(self, local_path: str, name: str, options: dict | None = None):
        raise HubError(key="Comando non disponibile per questa stampante")

    def job_thumbnail(self, raw: dict | None) -> bytes | None:
        """Miniatura PNG della stampa in corso fornita dalla stampante, se l'API la espone."""
        return None

    # ------------------------------------------------------------------ consumo di filamento (facoltativo)
    def job_plan(self, raw: dict | None) -> list[dict] | None:
        """Consumo previsto dell'intero lavoro in corso, ricavato dalla stampante (metadati o G-code scaricato).
        Serve per le stampe non avviate dall'archivio; per quelle non terminate si scala con l'avanzamento."""
        return None

    def usage_snapshot(self, raw: dict | None) -> dict | None:
        """Valori misurati a inizio stampa (es. peso delle bobine), da confrontare a fine stampa."""
        return None

    def usage_since(self, snapshot: dict | None, raw: dict | None) -> list[dict] | None:
        """Consumo reale misurato dalla stampante dall'inizio della stampa (anche se fallita o annullata)."""
        return None

    def fetch_gcode_meta(self, path: str, name: str, headers: dict | None = None) -> dict | None:
        """Scarica inizio e fine del G-code in stampa (richieste Range, senza trasferire tutto il file quando la
        stampante lo consente) e ne legge tempo, filamenti, tipi e colori come per i file dell'archivio."""
        import os
        import tempfile
        from ..gcodemeta import CHUNK, parse_gcode
        url = path if path.startswith("http") else self.base + path
        try:
            r = self._s.get(url, headers={**(headers or {}), "Range": f"bytes=0-{CHUNK - 1}"}, timeout=30, stream=True)
            if r.status_code == 206:
                data = r.content
                t = self._s.get(url, headers={**(headers or {}), "Range": f"bytes=-{CHUNK}"}, timeout=30)
                if t.status_code == 206:
                    data += b"\n" + t.content
            elif r.status_code == 200:            # la stampante ignora Range: si legge tutto (fino a 200 MB)
                buf = bytearray()
                for chunk in r.iter_content(1 << 16):
                    buf += chunk
                    if len(buf) > 200 * 1024 * 1024:
                        return None
                data = bytes(buf)
            else:
                return None
        except requests.RequestException:
            return None
        suffix = ".bgcode" if name.lower().endswith(".bgcode") else ".gcode"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp = fh.name
        try:
            return parse_gcode(tmp)
        except Exception:  # noqa: BLE001 - file non leggibile: nessun dato
            return None
        finally:
            os.unlink(tmp)


    def set_temperature(self, target: str, value: float):
        raise HubError(key="Comando non disponibile per questa stampante")

    def set_speed(self, pct: int):
        raise HubError(key="Comando non disponibile per questa stampante")

    # ------------------------------------------------------------------ supporto
    def url(self, path: str) -> str:
        return self.base + path

    def request(self, method: str, path: str, ok=(200, 201, 202, 204), **kw) -> requests.Response:
        try:
            r = self._s.request(method, self.url(path), timeout=kw.pop("timeout", TIMEOUT), **kw)
        except requests.RequestException as e:
            raise HubError(key="Stampante non raggiungibile: {e}", params={"e": e}) from e
        if r.status_code in (401, 403):
            raise HubError(key="Accesso negato dalla stampante: controlla chiave API o password")
        if r.status_code not in ok:
            raise HubError(key="Risposta non valida dalla stampante (codice {c})", params={"c": r.status_code})
        return r

    def snapshot(self) -> bytes | None:
        url = self.snapshot_url or self.auto_snapshot_url
        if not url:
            return None
        if url.startswith("/"):
            url = self.base + url
        try:
            r = self._s.get(url, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        return r.content if r.status_code == 200 and r.content[:2] in (b"\xff\xd8", b"\x89P") else None

    def has_snapshot(self) -> bool:
        return bool(self.snapshot_url or self.auto_snapshot_url)


def nozzle(i: int, label: str, temp, target, **extra) -> dict:
    return {"id": i, "side": "tool", "label": label, "temp": temp, "target": target or 0, "diameter": None,
            "flow": None, "material": None, "code": "", "fault": False, **extra}


def bed(i: int, label: str, temp, target, key: str = "", fault: bool = False) -> dict:
    """Un piano riscaldato: le stampanti grandi (es. Elegoo OrangeStorm Giga) ne hanno più di uno."""
    return {"id": i, "label": label, "temp": temp, "target": target or 0, "key": key or (f"bed:{i}" if i else "bed"),
            "fault": fault}


def base_state(client: DirectClient, online: bool, error: str = "", **kw) -> dict:
    """Stato normalizzato con tutti i campi attesi dall'interfaccia; i connettori passano solo ciò che conoscono."""
    state = kw.pop("state", "IDLE") if online else "OFFLINE"
    task = kw.pop("task_name", "")
    st = {
        "sn": client.sn, "kind": client.kind, "kind_label": client.label, "direct": True,
        "model": client.label, "name": client.name, "ip": client.base.split("://", 1)[-1],
        "online": online, "mqtt": 2 if online else 0, "state": state,
        "active": online and state in ("PREPARE", "RUNNING", "PAUSE") and bool(task),
        "progress": 0, "remaining_min": 0, "prepare_percent": 0, "layer": 0, "total_layers": 0,
        "task_name": task, "task_id": task, "subtask_id": "",
        "nozzle": None, "nozzle_target": None, "bed": None, "bed_target": None, "beds": [], "chamber": None, "chamber_target": 0,
        "status_text": "",
        "extruders": [], "speed_lvl": None, "speed_pct": 100, "wifi": "", "nozzle_diameter": "", "nozzle_type": "",
        "ams_busy": False, "trays": [], "ams_units": [], "nozzles": [], "dual_nozzle": False,
        "fans": {"part": None, "aux": None, "chamber": None}, "hms": [], "print_error": "", "firmware": "",
        "fw_min": "", "upgrade_state": "", "last_cmd_error": error, "cmd_retry": 0, "last_result": None,
        "liveview_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if client.has_snapshot() else "",
        "caps": dict(client.caps),
    }
    st.update(kw)
    st["caps"].setdefault("print_options", [])
    st["brand"] = client.brand
    st["printer_model"] = client.printer_model or st.get("model") or ""
    if st["nozzles"]:
        st["nozzle"], st["nozzle_target"] = st["nozzles"][0]["temp"], st["nozzles"][0]["target"]
    if st["beds"] and st["bed"] is None:
        st["bed"], st["bed_target"] = st["beds"][0]["temp"], st["beds"][0]["target"]
    return st


def plan_from_meta(meta: dict | None) -> list[dict] | None:
    """Piano di consumo dai metadati di un G-code (stessa forma usata per i file dell'archivio)."""
    fils = ((meta or {}).get("plates") or [{}])[0].get("filaments") or []
    plan = [{"type": f.get("type") or "?", "brand": "", "color": f.get("color") or "", "tray_info_idx": "",
             "grams": float(f.get("used_g") or 0), "meters": float(f.get("used_m") or 0), "ams_id": None,
             "slot_id": f.get("tool")} for f in fils if f.get("used_g") or f.get("used_m")]
    for f in plan:
        if not f["grams"] and f["meters"]:            # solo la lunghezza: peso stimato (1,75 mm, 1,24 g/cm³)
            f["grams"] = round(mm_to_grams(f["meters"] * 1000), 2)
    return plan or None


def mm_to_grams(mm: float, diameter: float = 1.75, density: float = 1.24) -> float:
    """Peso di un filamento di cui si conosce solo la lunghezza (densità tipica del PLA se non indicata)."""
    return mm * 3.14159 * (diameter / 2) ** 2 * density / 1000
