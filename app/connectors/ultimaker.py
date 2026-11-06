"""Stampanti UltiMaker: unione della API della stampante e di UltiMaker Connect.

Base (tutti i modelli: UM3, S3, S5, S7, S6, S8, Factor 4), API /api/v1 della stampante:
  stato       GET /api/v1/printer (stato, piano, testine, ugelli), GET /api/v1/print_job (404 = nessun lavoro),
              GET /api/v1/system/name|firmware|variant
  comandi     PUT /api/v1/print_job/state {"target": "pause"|"print"|"abort"}
              PUT /api/v1/printer/bed/temperature, PUT /api/v1/printer/heads/0/extruders/{n}/hotend/temperature
  stampa      POST /api/v1/print_job (multipart: jobname, file .gcode / .gcode.gz / .ufp)
  camera      GET /api/v1/camera/0/snapshot
Integrazione UltiMaker Connect (se risponde; sulla stampante "host" del gruppo):
  /cluster-api/v1/printers/          materiali caricati (marca, tipo, colore), Material Station, manutenzione, guasti
  /cluster-api/v1/print_jobs/printing  lavoro in corso → anteprima (/print_jobs/{uuid}/preview_image)
  /material-station-api/v1/material-station/   umidità e bobine della Material Station
  /fault-api/v1/faults/              errori e avvisi della stampante
Autorizzazione: i comandi richiedono Digest (SHA-256). Si ottiene con POST /api/v1/auth/request e
l'approvazione sul display della stampante; FleetBuddy la richiede dal modulo in Amministrazione.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from requests.auth import HTTPDigestAuth

from ..hub_client import HubError
from .base import DirectClient, base_state, bed, nozzle

JOB_STATE = {"printing": "RUNNING", "pre_print": "PREPARE", "pausing": "PAUSE", "paused": "PAUSE", "resuming": "RUNNING",
             "post_print": "RUNNING", "wait_cleanup": "FINISH", "wait_user_action": "PAUSE", "sent_to_printer": "PREPARE"}
PRINTER_STATE = {"idle": "IDLE", "printing": "RUNNING", "error": "FAILED", "maintenance": "PREPARE", "booting": "PREPARE",
                 "pre_print": "PREPARE", "unreachable": "OFFLINE"}
RESULT = {"finished": "finished", "aborted": "cancelled", "failed": "failed"}
COLORS = {"black": "#1A1A1A", "white": "#F5F5F5", "red": "#D32F2F", "blue": "#1E5BD8", "green": "#2E9E44", "yellow": "#F2C230",
          "orange": "#F07C1E", "grey": "#8C8C8C", "gray": "#8C8C8C", "silver": "#BDBDBD", "natural": "#EDE6D3",
          "transparent": "#E8F1F2", "purple": "#7B3FA0", "pink": "#E85A9B", "brown": "#7A4A2A", "magenta": "#C2187A"}


def _color(name: str | None) -> str:
    if not name:
        return "#888888"
    if name.startswith("#"):
        return name[:7].upper()
    low = name.lower()
    return next((v for k, v in COLORS.items() if k in low), "#888888")


class UltimakerClient(DirectClient):
    kind = "ultimaker"
    label = "UltiMaker"
    default_brand = "UltiMaker"
    caps = {"temperature": True, "speed": False, "filament": False, "bed_clean": False, "print_options": [],
            "upload": True, "ufp": True, "chamber_heater": False}

    def __init__(self, row: dict):
        super().__init__(row)
        if self.username and self.password:
            self._s.auth = HTTPDigestAuth(self.username, self.password)
        self.system: dict = {}
        self.ip = urlsplit(self.base).hostname
        self._cache: dict[str, tuple[float, object]] = {}
        self._connect_ok: bool | None = None       # None = ancora da provare
        self._connect_retry = 0.0
        self._thumb: tuple[str, bytes | None] = ("", None)

    # ------------------------------------------------------------------ supporto
    def _get(self, path: str, allow_404=True):
        r = self.request("GET", path, ok=(200, 404) if allow_404 else (200,))
        if r.status_code == 404:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text.strip().strip('"')

    def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            val = fn()
        except HubError:
            val = None
        self._cache[key] = (time.time(), val)
        return val

    def _auth_error(self, e: HubError):
        if not self._s.auth:
            raise HubError(key="La stampante UltiMaker richiede l'autorizzazione: usa \"Richiedi autorizzazione\" in Amministrazione") from e
        raise e

    # ------------------------------------------------------------------ lettura
    def model(self) -> dict:
        if not self.system:
            self.system = {k: self._get(f"/api/v1/system/{k}") for k in ("name", "firmware", "variant")}
        raw = {"system": self.system, "printer": self._get("/api/v1/printer", allow_404=False),
               "job": self._get("/api/v1/print_job")}
        # UltiMaker Connect: facoltativo (risponde sulla stampante host del gruppo); si riprova ogni 5 minuti se manca
        if self._connect_ok is not False or time.time() > self._connect_retry:
            printers = self._cached("cluster_printers", 10, lambda: self._get("/cluster-api/v1/printers/"))
            self._connect_ok = isinstance(printers, list)
            if self._connect_ok:
                mine = [p for p in printers if p.get("ip_address") == self.ip] or (printers if len(printers) == 1 else [])
                raw["cluster_printer"] = mine[0] if mine else None
                jobs = self._cached("cluster_jobs", 10, lambda: self._get("/cluster-api/v1/print_jobs/printing")) or []
                uid = (raw["cluster_printer"] or {}).get("uuid")
                raw["cluster_job"] = next((j for j in jobs if uid and uid in (j.get("printer_uuid"), j.get("assigned_to"))), None)
            else:
                self._connect_retry = time.time() + 300
        raw["faults"] = self._cached("faults", 30, lambda: self._get("/fault-api/v1/faults/"))
        raw["material_station"] = self._cached("misp", 30, lambda: self._get("/material-station-api/v1/material-station/"))
        return raw

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error, model="UltiMaker")
        pr, job, sysd = raw.get("printer") or {}, raw.get("job") or {}, raw.get("system") or {}
        cp = raw.get("cluster_printer") or {}
        jstate = (job.get("state") or "none").lower()
        if job and jstate in JOB_STATE:
            state = JOB_STATE[jstate]
        else:
            state = PRINTER_STATE.get((pr.get("status") or "idle").lower(), "IDLE")
        name = job.get("name") or ""
        result = RESULT.get((job.get("result") or "").lower())
        conf = {c.get("extruder_index"): c for c in cp.get("configuration") or []}
        noz = []
        for i, ex in enumerate(((pr.get("heads") or [{}])[0] or {}).get("extruders") or []):
            hot = ex.get("hotend") or {}
            temp = hot.get("temperature") or {}
            core = hot.get("id") or (conf.get(i) or {}).get("print_core_id") or ""
            m = re.search(r"(\d+\.\d+)", core)
            mat = (conf.get(i) or {}).get("material") or {}
            noz.append(nozzle(i, f"{i + 1}", temp.get("current"), temp.get("target"), type_name=core or None,
                              diameter=float(m.group(1)) if m else None,
                              material_label=" ".join(x for x in (mat.get("brand"), mat.get("material"), mat.get("color")) if x) or None,
                              material_color=_color(mat.get("color")) if mat else None))
        bt = ((pr.get("bed") or {}).get("temperature") or {})
        # Material Station (S5/S7 Pro Bundle): bobine come slot, con umidità e residuo
        ms_c = cp.get("material_station") or {}
        ms_s = raw.get("material_station") or {}
        slots = ms_c.get("material_slots") or []
        trays, units = [], []
        if slots or ms_s.get("slots"):
            for s_ in (slots or ms_s.get("slots") or []):
                mat = s_.get("material") or {}
                rem = s_.get("material_remaining")
                loaded = bool(mat) or bool(s_.get("material_guid"))
                trays.append({"ams_id": 0, "slot_id": s_.get("slot_index", len(trays)), "label": str(s_.get("slot_index", len(trays)) + 1),
                              "kind": "ams", "loaded": loaded, "type": mat.get("material") or ("?" if loaded else ""),
                              "color": _color(mat.get("color")) if loaded else "", "sub_brand": mat.get("brand") or "",
                              "remain": round(rem * 100) if isinstance(rem, (int, float)) and rem >= 0 else None,
                              "remain_g": None, "rfid": False, "idx": ""})
            units.append({"ams_id": 0, "kind": "Material Station", "letter": "", "nozzle": None, "temp": None,
                          "humidity": round(ms_s["current_humidity"]) if isinstance(ms_s.get("current_humidity"), (int, float)) else None})
        alerts = []
        for f in (raw.get("faults") or []) + (cp.get("faults") or []):
            if isinstance(f, dict) and (f.get("message") or f.get("code")):
                alerts.append({"text": f"{f.get('code', '')}: {f.get('message', '')}".strip(": ")})
        if cp.get("maintenance_required"):
            alerts.append({"msg": "La stampante richiede manutenzione"})
        if jstate == "wait_user_action":
            alerts.append({"msg": "La stampante richiede un intervento sul display"})
        thumb_job = (raw.get("cluster_job") or {}).get("uuid") or ""
        return base_state(
            self, True, error, state=state, task_name=name if (state in ("RUNNING", "PAUSE", "PREPARE", "FINISH") or result) else "",
            task_id=job.get("uuid") or name, progress=int(round((job.get("progress") or 0) * 100)),
            remaining_min=int(max(0, (job.get("time_total") or 0) - (job.get("time_elapsed") or 0)) // 60),
            nozzles=noz, beds=[bed(0, "", bt.get("current"), bt.get("target"))],
            trays=trays, ams_units=units, firmware=str(sysd.get("firmware") or cp.get("firmware_version") or ""),
            model=str(sysd.get("variant") or cp.get("machine_variant") or "UltiMaker"),
            alerts=alerts + ([{"msg": "Rimuovi la stampa e conferma sul display della stampante"}] if jstate == "wait_cleanup" else []), last_result=result if name else None,
            caps={**self.caps, "connect": bool(self._connect_ok), "authorized": bool(self._s.auth), "thumb_job": thumb_job})

    def info(self, raw: dict) -> dict:
        s = raw.get("system") or {}
        return {"board": str(s.get("variant") or "UltiMaker"), "firmware": str(s.get("firmware") or ""),
                "mode": "Connect" if self._connect_ok else "API /api/v1",
                "status": (raw.get("printer") or {}).get("status"),
                "tools": len((((raw.get("printer") or {}).get("heads") or [{}])[0] or {}).get("extruders") or [])}

    def job_thumbnail(self, raw: dict | None) -> bytes | None:
        uid = ((raw or {}).get("cluster_job") or {}).get("uuid")
        if not uid:
            return None
        if self._thumb[0] == uid:
            return self._thumb[1]
        try:
            r = self.request("GET", f"/cluster-api/v1/print_jobs/{uid}/preview_image")
            img = r.content if r.content[:8] == b"\x89PNG\r\n\x1a\n" or r.content[:2] == b"\xff\xd8" else None
        except HubError:
            img = None
        self._thumb = (uid, img)
        return img

    def snapshot(self) -> bytes | None:
        if self.snapshot_url:
            return super().snapshot()
        try:
            r = self._s.get(self.base + "/api/v1/camera/0/snapshot", timeout=6, allow_redirects=True)
        except Exception:  # noqa: BLE001
            return None
        return r.content if r.status_code == 200 and r.content[:2] == b"\xff\xd8" else None

    def has_snapshot(self) -> bool:
        return True

    # ------------------------------------------------------------------ comandi
    def _state(self, target: str):
        try:
            self.request("PUT", "/api/v1/print_job/state", json={"target": target})
        except HubError as e:
            self._auth_error(e)

    def pause(self):
        self._state("pause")

    def resume(self):
        self._state("print")

    def stop(self):
        self._state("abort")

    def set_temperature(self, target: str, value: float):
        v = max(0.0, min(350.0, float(value)))
        try:
            if target == "bed":
                self.request("PUT", "/api/v1/printer/bed/temperature", data={"temperature": v})
            elif target.startswith("tool:"):
                self.request("PUT", f"/api/v1/printer/heads/0/extruders/{int(target[5:])}/hotend/temperature", data={"temperature": v})
            else:
                raise HubError(key="Riscaldatore non valido")
        except HubError as e:
            self._auth_error(e)

    def start(self, local_path: str, name: str, options: dict | None = None):
        jobname = re.sub(r"\.(gcode\.gz|gcode|ufp)$", "", name, flags=re.I)
        try:
            with open(local_path, "rb") as fh:
                self.request("POST", "/api/v1/print_job", data={"jobname": jobname, "owner": "FleetBuddy"},
                             files={"file": (name, fh, "application/octet-stream")}, timeout=600)
        except HubError as e:
            self._auth_error(e)

    # ------------------------------------------------------------------ autorizzazione
    def request_auth(self, user: str) -> dict:
        r = self.request("POST", "/api/v1/auth/request", data={"application": "FleetBuddy", "user": user[:60]})
        return r.json()

    def check_auth(self, auth_id: str) -> str:
        d = self._get(f"/api/v1/auth/check/{auth_id}") or {}
        return (d.get("message") if isinstance(d, dict) else str(d)) or "unknown"
