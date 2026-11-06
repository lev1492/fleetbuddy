"""Stampanti INTAMSYS con l'API "INTAMTOUCH Remote Access" (porta 5588), es. FUNMAT PRO 610HT / 410.

Accesso:    GET /v1/login?signin=md5("password=…") → token
Stato:      /printer/system, /printer/runningstatus, /printer/basictemperature (ugelli, piano, camera, camere
            filamento, raffreddamento ad acqua), /printer/filamentweight, /printer/currentalarm, /job/currentjob
Comandi:    POST /job/currentjob/pause | resume | abort
Stampa:     l'API non permette di caricare file né di cambiare temperature: si avviano i file presenti sulla
            stampante (/fileops/printfiles, POST /job/newjob/start?name=…)
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import time

from ..hub_client import HubError
from .base import DirectClient, base_state, bed, nozzle

JOB = {"printing": "RUNNING", "paused": "PAUSE", "pausing": "PAUSE", "resuming": "RUNNING", "preheat": "PREPARE",
       "preheating": "PREPARE", "heat-holding": "PREPARE", "heating": "PREPARE", "maintaining": "PREPARE"}
RESULT = {"completed": "finished", "aborted": "cancelled", "stopped": "cancelled"}


class IntamsysClient(DirectClient):
    kind = "intamsys"
    label = "INTAMSYS (INTAMTOUCH)"
    default_brand = "INTAMSYS"
    default_port = 5588
    caps = {"temperature": False, "speed": False, "filament": False, "bed_clean": False, "print_options": [],
            "upload": False, "remote_files": True, "chamber_heater": False}

    def __init__(self, row: dict):
        super().__init__(row)
        self.token = ""
        self._signin_ok = ""
        self.system: dict = {}
        self._weights: tuple[float, dict] = (0.0, {})

    def _login(self):
        """Il documento indica signin=md5("password=…"), ma i firmware in uso accettano la password in chiaro
        (come il client web di INTAMSYS): si prova prima così, poi le varianti cifrate; si ricorda quella giusta."""
        variants = [self.password, hashlib.md5(f"password={self.password}".encode()).hexdigest(),
                    hashlib.md5(self.password.encode()).hexdigest()]
        order = [self._signin_ok] + [v for v in variants if v != self._signin_ok] if self._signin_ok else variants
        for signin in order:
            d = self.request("GET", "/v1/login", params={"signin": signin}).json()
            tok = (d.get("data") or {}).get("token") if isinstance(d, dict) and "error" not in d else None
            if tok:
                self.token, self._signin_ok = tok, signin
                return
        raise HubError(key="Password della stampante INTAMSYS errata")

    def _api(self, method: str, path: str, params: dict | None = None, retry=True) -> dict:
        if not self.token:
            self._login()
        d = self.request(method, "/v1" + path, params={"token": self.token, **(params or {})}).json()
        # l'API usa "status" in modi diversi (1, true, altri numeri): fa fede la presenza di "error"
        err = d.get("error") if isinstance(d, dict) else None
        if not err:
            return d.get("data") or {}
        code = err.get("err_code", err.get("code"))
        if code in (401, 50000) and retry:              # token non valido o scaduto: nuovo accesso
            self.token = ""
            return self._api(method, path, params, retry=False)
        raise HubError(key="Errore dalla stampante: {e}", params={"e": err.get("message") or err.get("msg") or code})

    def model(self) -> dict:
        if not self.system:
            self.system = self._api("GET", "/printer/system")
        raw = {"system": self.system, "running": self._api("GET", "/printer/runningstatus"),
               "temps": self._api("GET", "/printer/basictemperature")}
        for key, path in (("job", "/job/currentjob"), ("alarm", "/printer/currentalarm")):
            try:
                raw[key] = self._api("GET", path)
            except HubError:
                raw[key] = {}
        if time.time() - self._weights[0] > 60:          # il peso del filamento cambia lentamente
            try:
                self._weights = (time.time(), self._api("GET", "/printer/filamentweight"))
            except HubError:
                self._weights = (time.time(), {})
        raw["weights"] = self._weights[1]
        return raw

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error, model="INTAMSYS")
        t, job, sysd = raw.get("temps") or {}, raw.get("job") or {}, raw.get("system") or {}
        rs = str((raw.get("running") or {}).get("running_status") or "idle").lower()
        js = str(job.get("job_status") or "").lower()
        if js in JOB and rs != "idle":
            state = JOB[js]
        elif rs == "idle":
            state = "IDLE"
        elif "pause" in rs:
            state = "PAUSE"
        elif "print" in rs:
            state = "RUNNING"
        elif "error" in rs or "alarm" in rs:
            state = "FAILED"
        else:
            state = "PREPARE"                            # calibrazione, manutenzione, preparazione
        fname = job.get("file_name") or ""
        task = posixpath.basename(fname).rsplit(".", 1)[0] if fname else ""
        result = RESULT.get(js)
        num = lambda v: float(v) if isinstance(v, (int, float)) or (isinstance(v, str) and re.match(r"^-?\d+(\.\d+)?$", v)) else None
        noz = []
        for k in sorted((k for k in t if re.match(r"nozzle\d+$", k)), key=lambda k: int(k[6:])):
            n = int(k[6:]) - 1
            noz.append(nozzle(n, f"{n + 1}", num(t[k].get("current_temperature")), num(t[k].get("target_temperature")) or 0))
        bp, ch = t.get("buildplate") or {}, t.get("chamber") or {}
        weights = raw.get("weights") or {}
        extra = []
        for k in sorted((k for k in t if re.match(r"filament\d+$", k)), key=lambda k: int(k[8:])):
            w = (weights.get(k) or {}).get("current_weight")
            extra.append({"kind": "filament", "n": int(k[8:]), "temp": num(t[k].get("current_temperature")),
                          "target": num(t[k].get("target_temperature")) or 0, "weight": num(w)})
        if "water_cooling" in t:
            extra.append({"kind": "water", "temp": num((t.get("water_cooling") or {}).get("current_temperature"))})
        alarm = raw.get("alarm") or {}
        alerts = [{"text": f"{alarm.get('description', '')} – {alarm.get('action', '')}".strip(" –")}] if alarm.get("code") not in (0, "0", None) else []
        total, done = num(job.get("estimated_total_time")) or 0, num(job.get("real_printed_time")) or 0
        return base_state(
            self, True, error, state=state, task_name=task if (state in ("RUNNING", "PAUSE", "PREPARE") or (result and rs != "idle")) else "",
            task_id=task, progress=int(min(100, num(job.get("print_progress")) or 0)),
            remaining_min=int(max(0, total - done) // 60), nozzles=noz, dual_nozzle=len(noz) > 1,
            beds=[bed(0, "", num(bp.get("current_temperature")), num(bp.get("target_temperature")) or 0)],
            chamber=num(ch.get("current_temperature")), chamber_target=num(ch.get("target_temperature")) or 0,
            extra_temps=extra, firmware=sysd.get("firmware_version") or "", model=sysd.get("model") or "INTAMSYS",
            alerts=alerts, last_result=result if fname else None)

    def info(self, raw: dict) -> dict:
        s = raw.get("system") or {}
        return {"board": s.get("model") or "INTAMSYS", "firmware": s.get("firmware_version"),
                "mode": f"API {s.get('api_version', '')} · {s.get('software_version', '')}".strip(" ·"),
                "status": (raw.get("running") or {}).get("running_status"),
                "tools": len([k for k in (raw.get("temps") or {}) if k.startswith("nozzle")])}

    def pause(self):
        self._api("POST", "/job/currentjob/pause")

    def resume(self):
        self._api("POST", "/job/currentjob/resume")

    def stop(self):
        self._api("POST", "/job/currentjob/abort")

    def list_files(self, folder: str = "") -> dict:
        d = self._api("GET", "/fileops/printfiles", params={"num": 500, "sort": "time_descending"})
        return {"dir": "", "dirs": [], "files": [f for f in d.get("file_list") or [] if isinstance(f, str)]}

    def start_remote(self, path: str):
        self._api("POST", "/job/newjob/start", params={"name": posixpath.basename(path)})
