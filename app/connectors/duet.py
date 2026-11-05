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
from .base import DirectClient, base_state, bed, nozzle

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

    def start(self, local_path: str, name: str, options: dict | None = None):
        dest = self.upload(local_path, name)
        self.gcode(f'M32 "{dest}"')

    def job_thumbnail(self, raw: dict | None) -> bytes | None:
        """Miniatura della stampa in corso dall'object model (job.file.thumbnails, QOI o PNG). In modalità
        autonoma i dati non sono inclusi nel modello e si leggono a pezzi con rr_thumbnail."""
        import base64
        from ..imgutil import qoi_to_png
        jf = ((raw or {}).get("job") or {}).get("file") or {}
        thumbs = sorted((t for t in jf.get("thumbnails") or [] if t), key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
        if not thumbs:
            return None
        t = thumbs[-1]
        data = t.get("data")
        if not data and self.mode == "standalone" and t.get("offset") is not None:
            parts, offset = [], t["offset"]
            for _ in range(200):
                r = self._req("GET", "/rr_thumbnail", params={"name": jf.get("fileName"), "offset": offset}).json()
                if r.get("err"):
                    return None
                parts.append(r.get("data") or "")
                offset = r.get("next") or 0
                if not offset:
                    break
            data = "".join(parts)
        if not data:
            return None
        try:
            img = base64.b64decode(data)
            return qoi_to_png(img) if (t.get("format") or "").lower() == "qoi" or img[:4] == b"qoif" else img
        except Exception:  # noqa: BLE001 - miniatura danneggiata: semplicemente non la si mostra
            return None

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
        elif target.startswith("bed:"):                  # piani multipli: M140 P<indice del piano>
            self.gcode(f"M140 P{int(target[4:])} S{v:g}")
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
    fault = (h.get("state") or "") == "fault" or (h.get("current") or 0) >= 1999   # 2000 °C = sensore scollegato
    return {"temp": None if fault else h.get("current"), "fault": fault,
            "target": h.get("active") if (h.get("state") or "") not in ("off", "fault") else 0}


def _mapped(heat: dict, old_key: str, new_key: str) -> list[int]:
    """Indici dei riscaldatori di piano o camera: RRF ≤ 3.6 usa bedHeaters/chamberHeaters (lista con -1 per
    "nessuno"), RRF 3.7 usa bedHeaterMapping/chamberHeaterMapping (lista di liste)."""
    if heat.get(new_key) is not None:
        return [int(x) for grp in heat.get(new_key) or [] for x in (grp if isinstance(grp, list) else [grp]) if x is not None and x >= 0]
    return [int(x) for x in heat.get(old_key) or [] if x is not None and x >= 0]


def _fx(g: dict, key: str, i: int, default=None):
    v = g.get(key)
    return v[i] if isinstance(v, list) and i < len(v) else default


def normalize_duet(client: DuetClient, model: dict | None, error: str = "") -> dict:
    """Stato della stampante Duet nella stessa forma usata per le Bambu (vedi printers.normalize)."""
    if not model:
        return base_state(client, False, error, model="Duet")
    m = model
    state_raw = ((m.get("state") or {}).get("status") or "").lower()
    state = STATE.get(state_raw, "UNKNOWN")
    online = state != "OFFLINE"
    job = m.get("job") or {}
    jf = job.get("file") or {}
    size = jf.get("size") or 0
    progress = int(min(100, 100 * (job.get("filePosition") or 0) / size)) if size else 0
    left = job.get("timesLeft") or {}
    remaining = left.get("slicer") or left.get("file") or left.get("filament") or 0
    g = m.get("global") or {}                         # variabili globali (es. FabbrixOS: fxToolNozzle, fxNozzleTypes…)
    monitors = (m.get("sensors") or {}).get("filamentMonitors") or []
    current_tool = (m.get("state") or {}).get("currentTool", -1)
    tools = [t for t in (m.get("tools") or []) if t]
    nozzles = []
    for t in tools:
        num = t.get("number", len(nozzles))
        heaters = t.get("heaters") or []
        h = _heater(m, heaters[0]) if heaters else {}
        ntype = _fx(g, "fxNozzleTypes", int(_fx(g, "fxToolNozzleType", num, -1) or 0)) if g.get("fxNozzleTypes") else None
        ext = t.get("filamentExtruder", (t.get("extruders") or [num])[0] if t.get("extruders") else num)
        mon = monitors[ext] if isinstance(ext, int) and 0 <= ext < len(monitors) and monitors[ext] else None
        nozzles.append(nozzle(
            num, t.get("name") or f"T{num}", h.get("temp"), h.get("target"), fault=h.get("fault", False),
            diameter=_fx(g, "fxToolNozzle", num) or _fx(g, "fxNozzle", num),
            type_name=ntype, abrasive=bool(_fx(g, "fxNozzleTypeAbrasive", int(_fx(g, "fxToolNozzleType", num, 0) or 0), False)),
            active=num == current_tool, present=_fx(g, "fxToolPresent", num, True),
            filament_sensor=None if not mon or mon.get("enableMode") == 0 else
            ("ok" if mon.get("filamentPresent") and mon.get("status") == "ok" else "empty"),
            hot_hours=round(_fx(g, "fxToolHotSeconds", num, 0) / 3600, 1) if g.get("fxToolHotSeconds") else None))
    heat = m.get("heat") or {}
    bed_idx = _mapped(heat, "bedHeaters", "bedHeaterMapping")
    beds = []
    for n, hi in enumerate(bed_idx):
        h = _heater(m, hi)
        beds.append(bed(n, f"{n + 1}" if len(bed_idx) > 1 else "", h.get("temp"), h.get("target"),
                        key=f"bed:{n}" if len(bed_idx) > 1 else "bed", fault=h.get("fault", False)))
    ch_idx = _mapped(heat, "chamberHeaters", "chamberHeaterMapping")
    chamber = _heater(m, ch_idx[0]) if ch_idx else {}
    fans = m.get("fans") or []
    part = None
    tool_for_fan = next((t for t in tools if t.get("number") == current_tool), tools[0] if tools else None)
    if tool_for_fan and (tool_for_fan.get("fans") or []):
        fi = tool_for_fan["fans"][0]
        if fi < len(fans) and (fans[fi] or {}).get("actualValue") is not None:
            part = round(100 * fans[fi]["actualValue"])
    board = ((m.get("boards") or [{}])[0] or {})
    fname = PurePosixPath(jf.get("fileName") or "").name
    task = fname.rsplit(".", 1)[0] if fname else ""
    iface = next((i for i in ((m.get("network") or {}).get("interfaces") or []) if i and i.get("state") == "active" and i.get("rssi")), None)
    st_obj = m.get("state") or {}
    # motivi di pausa e avvisi comunicati dalla scheda (FabbrixOS e messaggi di RRF)
    alerts = [{"text": str(x)} for x in (g.get("fxPauseDetail") or g.get("fxPauseKind"), g.get("fxLastFilamentErrorReason"),
                                         g.get("fxDriverWarnText")) if x]
    faults = [n["label"] for n in nozzles if n["fault"]] + [f"{n + 1}" for n, b in enumerate(beds) if b["fault"]]
    if faults:
        alerts.append({"msg": "Sensore di temperatura guasto o scollegato: {t}", "params": {"t": ", ".join(faults)}})
    return base_state(
        client, online, error, model=board.get("name") or board.get("shortName") or "Duet",
        state=state, task_name=task if fname else "",
        task_id=f"{fname}:{job.get('lastFileName') or ''}" if fname else "",
        progress=progress, remaining_min=int(remaining // 60) if remaining else 0,
        layer=job.get("layer") or 0, total_layers=jf.get("numLayers") or 0,
        nozzles=nozzles, beds=beds, chamber=chamber.get("temp"), chamber_target=chamber.get("target") or 0,
        speed_pct=round(100 * ((m.get("move") or {}).get("speedFactor") or 1)),
        wifi=f"{iface['rssi']}dBm" if iface else "",
        fans={"part": part, "aux": None, "chamber": None},
        firmware=board.get("firmwareVersion") or "",
        status_text=st_obj.get("displayMessage") or "",
        alerts=alerts,
        last_result=("failed" if job.get("lastFileAborted") else "cancelled" if job.get("lastFileCancelled")
                     else "finished") if job.get("lastFileName") else None,
        caps={**client.caps, "mode": client.mode, "chamber_heater": bool(ch_idx)})
