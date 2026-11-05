"""Stampanti Raise3D con l'API "Raise Touch Remote Access" (porta 10800).

Va attivata sulla stampante: Impostazioni → Remote Access API (lì si imposta anche la password).
Accesso:    GET /v1/login con firma md5(sha1("password=…&timestamp=…")); il token dura 24 ore
Stato:      /printer/system, /printer/runningstatus, /printer/basic, /printer/nozzle1|2, /job/currentjob
Comandi:    /job/currentjob/set (pausa, ripresa, stop), /printer/nozzle1|2/temp/set, /printer/heatbedtemp/set,
            /printer/feedrate/set (velocità), /printer/fanspeed/set
Stampa:     l'API non permette di caricare file: si avviano file già presenti nella memoria della stampante
            (/fileops/list per sfogliarli, /job/newjob/set per avviarli)
Webcam:     /printer/camera indica indirizzo e credenziali del flusso video; FleetBuddy ne estrae un fotogramma
"""
from __future__ import annotations

import hashlib
import posixpath
import time

import requests

from ..hub_client import HubError
from .base import DirectClient, base_state, bed, nozzle

STATE = {"idle": "IDLE", "running": "RUNNING", "paused": "PAUSE", "busy": "PREPARE", "completed": "FINISH", "error": "FAILED"}


class Raise3DClient(DirectClient):
    kind = "raise3d"
    label = "Raise3D (Raise Touch)"
    default_brand = "Raise3D"
    default_port = 10800
    # upload False: i file si avviano dalla memoria della stampante (remote_files)
    caps = {"temperature": True, "speed": True, "filament": False, "bed_clean": False, "print_options": [],
            "upload": False, "remote_files": True, "chamber_heater": False}

    def __init__(self, row: dict):
        super().__init__(row)
        self.token = ""
        self.system: dict = {}
        self.camera: dict = {}
        self._sys_at = 0.0

    # ------------------------------------------------------------------ accesso
    def _login(self):
        ts = str(int(time.time() * 1000))
        sign = hashlib.md5(hashlib.sha1(f"password={self.password}&timestamp={ts}".encode()).hexdigest().encode()).hexdigest()
        d = self.request("GET", "/v1/login", params={"sign": sign, "timestamp": ts}).json()
        if d.get("status") != 1:
            raise HubError(key="Password dell'API Raise3D errata")
        self.token = (d.get("data") or {}).get("token", "")

    def _api(self, method: str, path: str, body: dict | None = None, params: dict | None = None, retry=True) -> dict:
        if not self.token:
            self._login()
        q = {"token": self.token, **(params or {})}
        r = self.request(method, "/v1" + path, params=q, json=body) if body is not None else self.request(method, "/v1" + path, params=q)
        d = r.json()
        if d.get("status") == 1:
            return d.get("data") or {}
        err = d.get("error") or {}
        if err.get("code") == 401 and retry:          # token scaduto (dura 24 ore): nuovo accesso
            self.token = ""
            return self._api(method, path, body, params, retry=False)
        raise HubError(key="Errore dalla stampante: {e}", params={"e": err.get("msg") or err.get("code")})

    # ------------------------------------------------------------------ lettura
    def model(self) -> dict:
        if not self.system or time.time() - self._sys_at > 600:
            self.system = self._api("GET", "/printer/system")
            try:
                self.camera = self._api("GET", "/printer/camera")
            except HubError:
                self.camera = {}
            self._sys_at = time.time()
        raw = {"system": self.system, "running": self._api("GET", "/printer/runningstatus"),
               "basic": self._api("GET", "/printer/basic"), "nozzle1": self._api("GET", "/printer/nozzle1")}
        if int(self.system.get("nozzies_num") or self.system.get("nozzles_num") or 1) > 1:   # "nozzies" è il nome nell'API
            raw["nozzle2"] = self._api("GET", "/printer/nozzle2")
        try:
            raw["job"] = self._api("GET", "/job/currentjob")
        except HubError:
            raw["job"] = {}
        return raw

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error, model="Raise3D")
        sysd, basic, job = raw.get("system") or {}, raw.get("basic") or {}, raw.get("job") or {}
        rs = (raw.get("running") or {}).get("running_status", "idle")
        state = STATE.get(rs, "IDLE")
        jstatus = job.get("job_status") or ""
        fname = job.get("file_name") or ""
        task = posixpath.basename(fname).rsplit(".", 1)[0] if fname else ""
        dual = "nozzle2" in raw
        noz = [nozzle(0, "", (raw.get("nozzle1") or {}).get("nozzle_cur_temp"), (raw.get("nozzle1") or {}).get("nozzle_tar_temp"),
                      side="left" if dual else "tool")]
        if dual:
            noz.append(nozzle(1, "", raw["nozzle2"].get("nozzle_cur_temp"), raw["nozzle2"].get("nozzle_tar_temp"), side="right"))
        show_job = bool(fname) and (state in ("RUNNING", "PAUSE", "PREPARE", "FINISH") or jstatus in ("completed", "stopped"))
        total, done = job.get("total_time") or 0, job.get("printed_time") or 0
        return base_state(
            self, True, error, state=state, task_name=task if show_job else "",
            task_id=job.get("job_id") or task, progress=int(min(100, job.get("print_progress") or 0)),
            remaining_min=int(max(0, total - done) // 60), layer=job.get("printed_layer") or 0,
            total_layers=job.get("total_layer") or 0, nozzles=noz, dual_nozzle=dual,
            beds=[bed(0, "", basic.get("heatbed_cur_temp"), basic.get("heatbed_tar_temp"))],
            speed_pct=int(basic.get("feed_cur_rate") or 100),
            fans={"part": basic.get("fan_cur_speed"), "aux": None, "chamber": None},
            firmware=sysd.get("firmware_version") or "", model=sysd.get("model") or "Raise3D",
            alerts=[{"msg": "Porta seriale scollegata o stampante in modalità sicura"}] if rs == "error" else [],
            last_result={"completed": "finished", "stopped": "cancelled"}.get(jstatus) if fname else None)

    def info(self, raw: dict) -> dict:
        s = raw.get("system") or {}
        return {"board": s.get("model") or "Raise3D", "firmware": s.get("firmware_version"),
                "mode": f"API {s.get('api_version', '')}".strip(), "status": (raw.get("running") or {}).get("running_status"),
                "tools": int(s.get("nozzies_num") or 1)}

    def snapshot(self) -> bytes | None:
        if self.snapshot_url:
            return super().snapshot()
        cam = self.camera or {}
        if not cam.get("is_camera_connected") or not cam.get("camerserver_URI"):
            return None
        from urllib.parse import urlsplit
        url = f"http://{urlsplit(self.base).hostname}{cam['camerserver_URI']}"
        try:   # primo fotogramma JPEG del flusso video protetto da utente e password
            with self._s.get(url, stream=True, timeout=5, auth=(cam.get("user_name", ""), cam.get("password", ""))) as r:
                buf = b""
                for chunk in r.iter_content(8192):
                    buf += chunk
                    a = buf.find(b"\xff\xd8")
                    b_ = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
                    if b_ > 0:
                        return buf[a:b_ + 2]
                    if len(buf) > 2_000_000:
                        break
        except requests.RequestException:
            return None
        return None

    def has_snapshot(self) -> bool:
        return bool(self.snapshot_url or (self.camera or {}).get("is_camera_connected"))

    # ------------------------------------------------------------------ comandi
    def _job(self, operate: str):
        self._api("POST", "/job/currentjob/set", {"operate": operate})

    def pause(self):
        self._job("pause")

    def resume(self):
        self._job("resume")

    def stop(self):
        self._job("stop")

    def set_temperature(self, target: str, value: float):
        v = int(max(0, float(value)))
        if target == "bed":
            self._api("POST", "/printer/heatbedtemp/set", {"temperature": min(v, 110)})
        elif target in ("tool:0", "tool:1"):
            self._api("POST", f"/printer/nozzle{int(target[5:]) + 1}/temp/set", {"temperature": min(v, 310)})
        else:
            raise HubError(key="Riscaldatore non valido")

    def set_speed(self, pct: int):
        self._api("POST", "/printer/feedrate/set", {"feedrate": max(1, min(350, int(pct)))})

    def list_files(self, folder: str = "Local") -> dict:
        d = self._api("GET", "/fileops/list", params={"dir": folder, "start_pos": 0, "max_num": 500})
        return {"dir": folder, "dirs": d.get("dir_list") or [],
                "files": [f.get("file_name") for f in d.get("file_list") or [] if f.get("file_name")]}

    def start_remote(self, path: str):
        self._api("POST", "/job/newjob/set", {"file_path": path})
