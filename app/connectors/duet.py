"""Stampanti Duet / RepRapFirmware 3, collegate direttamente (senza Fleet Hub).

Usa la stessa API HTTP di Duet Web Control, in una delle due modalità, riconosciuta da sola:
  - scheda autonoma (Duet 2, Duet 3 senza SBC): /rr_connect, /rr_model, /rr_gcode, /rr_upload
  - SBC (Duet 3 con Raspberry Pi e DSF):        /machine/connect, /machine/status, /machine/code, /machine/file
Lo stato arriva dall'"object model" di RRF 3 (lo stesso mostrato dal plugin Object Model di DWC).
RepRapFirmware 2 (rr_status) non è supportato.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from pathlib import PurePosixPath

import requests

from ..hub_client import HubError
from .base import DirectClient

log = logging.getLogger("fleetbuddy.duet")

# stato RRF → stato usato da FleetBuddy (lo stesso vocabolario delle stampanti Bambu)
STATE = {"idle": "IDLE", "processing": "RUNNING", "simulating": "RUNNING", "resuming": "RUNNING",
         "paused": "PAUSE", "pausing": "PAUSE", "cancelling": "RUNNING", "busy": "PREPARE",
         "changingtool": "PREPARE", "starting": "PREPARE", "halted": "FAILED", "updating": "PREPARE",
         "off": "OFFLINE", "disconnected": "OFFLINE"}
TIMEOUT = 6


class DuetClient(DirectClient):
    kind = "duet"
    label = "Duet / RepRapFirmware 3"
    caps = {"temperature": True, "speed": True, "filament": False, "bed_clean": False}

    def __init__(self, row: dict):
        super().__init__(row)
        self.mode: str | None = None          # "standalone" oppure "sbc"
        self.session_key: str | None = None
        self._lock = threading.Lock()

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        return normalize_duet(self, raw, error)

    def info(self, raw: dict) -> dict:
        board = (raw.get("boards") or [{}])[0] or {}
        return {"board": board.get("name") or board.get("shortName") or "Duet", "firmware": board.get("firmwareVersion"),
                "mode": self.mode, "status": (raw.get("state") or {}).get("status"),
                "tools": len([t for t in raw.get("tools") or [] if t])}

    # ------------------------------------------------------------------ collegamento
    def _headers(self) -> dict:
        return {"X-Session-Key": self.session_key} if self.session_key else {}

    def connect(self):
        """Riconosce la modalità e apre la sessione (con la password della scheda, se impostata)."""
        q = urllib.parse.urlencode({"password": self.password or "reprap", "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        try:
            r = self._s.get(f"{self.base}/rr_connect?{q}", timeout=TIMEOUT)
        except requests.RequestException as e:
            raise HubError(key="Stampante non raggiungibile: {e}", params={"e": e}) from e
        if r.status_code == 200 and r.headers.get("content-type", "").startswith(("application/json", "text/")) \
                and r.text.lstrip().startswith("{"):
            data = r.json()
            if data.get("err") == 1:
                raise HubError(key="Password della scheda Duet errata")
            if data.get("err") == 2:
                raise HubError(key="La scheda Duet non accetta altre sessioni: chiudi qualche finestra di Duet Web Control")
            if int(data.get("apiLevel", 1)) < 1 and "sessionTimeout" not in data:
                raise HubError(key="Serve RepRapFirmware 3 o successivo")
            self.mode, self.session_key = "standalone", data.get("sessionKey")
            return
        # nessuna API rr_: probabilmente una Duet 3 con SBC (DSF)
        try:
            r = self._s.get(f"{self.base}/machine/connect", params={"password": self.password}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise HubError(key="Stampante non raggiungibile: {e}", params={"e": e}) from e
        if r.status_code == 403:
            raise HubError(key="Password della scheda Duet errata")
        if r.status_code not in (200, 204):
            raise HubError(key="Questo indirizzo non risponde come una scheda Duet (codice {c})", params={"c": r.status_code})
        self.mode = "sbc"
        try:
            self.session_key = (r.json() or {}).get("sessionKey") if r.content else None
        except ValueError:
            self.session_key = None

    def _req(self, method: str, path: str, *, retry=True, **kw) -> requests.Response:
        with self._lock:
            if not self.mode:
                self.connect()
        headers = {**self._headers(), **kw.pop("headers", {})}
        try:
            r = self._s.request(method, self.base + path, headers=headers, timeout=kw.pop("timeout", TIMEOUT), **kw)
        except requests.RequestException as e:
            self.mode = None
            raise HubError(key="Stampante non raggiungibile: {e}", params={"e": e}) from e
        if r.status_code in (401, 403) and retry:      # sessione scaduta: nuova connessione
            self.mode = None
            return self._req(method, path, retry=False, **kw)
        return r

    # ------------------------------------------------------------------ lettura
    def model(self) -> dict:
        if self.mode != "sbc":
            r = self._req("GET", "/rr_model", params={"flags": "d99vno"})
            if self.mode == "standalone":
                data = r.json()
                return data.get("result", data)
        r = self._req("GET", "/machine/status")
        if r.status_code != 200:
            raise HubError(key="Risposta non valida dalla scheda Duet (codice {c})", params={"c": r.status_code})
        return r.json()

    # ------------------------------------------------------------------ comandi
    def gcode(self, code: str) -> str:
        if self.mode != "sbc":
            r = self._req("GET", "/rr_gcode", params={"gcode": code})
            if self.mode == "standalone":
                if r.status_code != 200:
                    raise HubError(key="Comando rifiutato dalla scheda Duet (codice {c})", params={"c": r.status_code})
                return ""
        r = self._req("POST", "/machine/code", data=code.encode("utf-8"), headers={"Content-Type": "text/plain"})
        if r.status_code != 200:
            raise HubError(key="Comando rifiutato dalla scheda Duet (codice {c})", params={"c": r.status_code})
        return r.text

    def upload(self, local_path: str, name: str):
        dest = str(PurePosixPath("0:/gcodes") / name)
        with open(local_path, "rb") as fh:
            data = fh.read()
        if self.mode != "sbc":
            r = self._req("POST", "/rr_upload", params={"name": dest, "time": time.strftime("%Y-%m-%dT%H:%M:%S")},
                          data=data, timeout=600)
            if self.mode == "standalone":
                if r.status_code != 200 or (r.text.strip().startswith("{") and r.json().get("err")):
                    raise HubError(key="Caricamento del file sulla scheda Duet non riuscito")
                return dest
        r = self._req("PUT", "/machine/file/" + urllib.parse.quote(dest, safe=""), data=data, timeout=600)
        if r.status_code not in (200, 201, 204):
            raise HubError(key="Caricamento del file sulla scheda Duet non riuscito")
        return dest

    def start(self, local_path: str, name: str):
        dest = self.upload(local_path, name)
        self.gcode(f'M32 "{dest}"')

    def pause(self):
        self.gcode("M25")

    def resume(self):
        self.gcode("M24")

    def stop(self):
        """In RRF una stampa si annulla dopo averla messa in pausa: M25, attesa della pausa, poi M0."""
        self.gcode("M25")
        for _ in range(30):
            time.sleep(1)
            st = ((self.model().get("state") or {}).get("status") or "").lower()
            if st == "paused":
                break
        self.gcode("M0")

    def set_temperature(self, target: str, value: float):
        v = max(0, min(400, float(value)))
        if target == "bed":
            self.gcode(f"M140 S{v:g}")
        elif target == "chamber":
            self.gcode(f"M141 S{v:g}")
        elif target.startswith("tool:"):
            tool = int(target.split(":", 1)[1])
            self.gcode(f"G10 P{tool} S{v:g} R{v:g}")
        else:
            raise HubError(key="Riscaldatore non valido")

    def set_speed(self, pct: int):
        self.gcode(f"M220 S{max(10, min(300, int(pct)))}")


# ---------------------------------------------------------------------- stato normalizzato
def _heater(model: dict, idx) -> dict:
    heaters = (model.get("heat") or {}).get("heaters") or []
    try:
        h = heaters[int(idx)] or {}
    except (TypeError, ValueError, IndexError):
        return {}
    return {"temp": h.get("current"), "target": h.get("active") if (h.get("state") or "") != "off" else 0}


def normalize_duet(client: DuetClient, model: dict | None, error: str = "") -> dict:
    """Stato della stampante Duet nella stessa forma usata per le Bambu (vedi printers.normalize)."""
    m = model or {}
    state_raw = ((m.get("state") or {}).get("status") or "").lower()
    state = STATE.get(state_raw, "UNKNOWN") if model else "OFFLINE"
    online = bool(model) and state != "OFFLINE"
    job = m.get("job") or {}
    jf = job.get("file") or {}
    size = jf.get("size") or 0
    progress = int(min(100, 100 * (job.get("filePosition") or 0) / size)) if size else 0
    left = (job.get("timesLeft") or {})
    remaining = left.get("slicer") or left.get("file") or left.get("filament") or 0
    tools = [t for t in (m.get("tools") or []) if t]
    nozzles = []
    for t in tools:
        heaters = t.get("heaters") or []
        h = _heater(m, heaters[0]) if heaters else {}
        nozzles.append({"id": t.get("number", len(nozzles)), "side": "tool", "label": t.get("name") or f"T{t.get('number', len(nozzles))}",
                        "temp": h.get("temp"), "target": h.get("target"), "diameter": None, "flow": None,
                        "material": None, "code": "", "filament": t.get("filamentExtruder")})
    heat = m.get("heat") or {}
    bed = _heater(m, (heat.get("bedHeaters") or [-1])[0]) if (heat.get("bedHeaters") or [-1])[0] not in (-1, None) else {}
    ch_idx = (heat.get("chamberHeaters") or [-1])[0]
    chamber = _heater(m, ch_idx) if ch_idx not in (-1, None) else {}
    fans = [f for f in (m.get("fans") or []) if f]
    part = None
    if tools and (tools[0].get("fans") or []):
        fi = tools[0]["fans"][0]
        if fi < len(m.get("fans") or []) and (m["fans"][fi] or {}).get("actualValue") is not None:
            part = round(100 * m["fans"][fi]["actualValue"])
    board = ((m.get("boards") or [{}])[0] or {})
    fname = PurePosixPath(jf.get("fileName") or "").name
    task = fname.rsplit(".", 1)[0] if fname else ""
    return {
        "sn": client.sn, "kind": "duet", "kind_label": client.label, "direct": True, "model": board.get("name") or board.get("shortName") or "Duet",
        "name": client.name,
        "ip": client.base.split("://", 1)[-1], "online": online, "mqtt": 2 if online else 0,
        "state": state if online else "OFFLINE", "active": state in ("PREPARE", "RUNNING", "PAUSE") and bool(fname),
        "progress": progress, "remaining_min": int(remaining // 60) if remaining else 0,
        "prepare_percent": 0, "layer": job.get("layer") or 0, "total_layers": jf.get("numLayers") or 0,
        "task_name": task, "task_id": f"{fname}:{job.get('lastFileName') or ''}" if fname else "",
        "subtask_id": str(int(jf.get("printTime") or 0)) if fname else "",
        "nozzle": nozzles[0]["temp"] if nozzles else None, "nozzle_target": nozzles[0]["target"] if nozzles else None,
        "bed": bed.get("temp"), "bed_target": bed.get("target"),
        "chamber": chamber.get("temp"), "chamber_target": chamber.get("target") or 0,
        "extruders": [], "speed_lvl": None,
        "speed_pct": round(100 * ((m.get("move") or {}).get("speedFactor") or 1)),
        "wifi": "", "nozzle_diameter": "", "nozzle_type": "", "ams_busy": False, "trays": [], "ams_units": [],
        "nozzles": nozzles, "dual_nozzle": False,
        "fans": {"part": part, "aux": None, "chamber": None},
        "hms": [], "print_error": "", "firmware": board.get("firmwareVersion") or "", "fw_min": "",
        "upgrade_state": "", "liveview_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if client.snapshot_url else "",
        "last_cmd_error": error, "cmd_retry": 0,
        # esito dell'ultima stampa: la Duet torna "idle" subito, senza uno stato "terminata"
        "last_result": ("failed" if job.get("lastFileAborted") else "cancelled" if job.get("lastFileCancelled")
                        else "finished") if job.get("lastFileName") else None,
        "caps": {"temperature": True, "speed": True, "filament": False, "bed_clean": False, "mode": client.mode},
    }
