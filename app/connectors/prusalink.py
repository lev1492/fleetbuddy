"""PrusaLink, API v1 (Prusa MK4/MK4S, MK3.9, XL, MINI/MINI+, CORE One con firmware 5 o successivo).

Autenticazione: Digest con utente "maker" e la password mostrata sulla stampante (Impostazioni → Rete),
                oppure chiave API (X-Api-Key) sulle installazioni che la usano.
Stato:      GET /api/version, GET /api/v1/status (stato, temperature, velocità, ventole, avanzamento)
            GET /api/v1/job (file in stampa)
Comandi:    PUT /api/v1/job/{id}/pause | resume, DELETE /api/v1/job/{id}
Stampa:     PUT /api/v1/files/{memoria}/{file} con "Print-After-Upload: ?1"
Limiti:     l'API v1 non accetta G-code arbitrari, quindi temperature e velocità non sono modificabili da qui.
"""
from __future__ import annotations

import urllib.parse

from requests.auth import HTTPDigestAuth

from ..hub_client import HubError
from .base import DirectClient, base_state, nozzle

STATE = {"PRINTING": "RUNNING", "PAUSED": "PAUSE", "BUSY": "PREPARE", "ATTENTION": "PAUSE", "ERROR": "FAILED",
         "FINISHED": "IDLE", "STOPPED": "IDLE", "IDLE": "IDLE", "READY": "IDLE"}
RESULT = {"FINISHED": "finished", "STOPPED": "cancelled"}


class PrusaLinkClient(DirectClient):
    kind = "prusalink"
    label = "PrusaLink"
    caps = {"temperature": False, "speed": False, "filament": False, "bed_clean": False}

    def __init__(self, row: dict):
        super().__init__(row)
        if self.api_key:
            self._s.headers["X-Api-Key"] = self.api_key
        else:
            self._s.auth = HTTPDigestAuth(self.username or "maker", self.password)
        self._version: dict = {}
        self._storage = ""

    def model(self) -> dict:
        if not self._version:
            self._version = self.request("GET", "/api/version").json()
        status = self.request("GET", "/api/v1/status").json()
        job = {}
        if (status.get("job") or {}).get("id"):
            r = self.request("GET", "/api/v1/job", ok=(200, 204))
            job = r.json() if r.content else {}
        return {"version": self._version, "status": status, "job": job}

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error)
        st, job = (raw.get("status") or {}), (raw.get("job") or {})
        pr = st.get("printer") or {}
        pstate = (pr.get("state") or "IDLE").upper()
        state = STATE.get(pstate, "IDLE")
        f = job.get("file") or {}
        name = f.get("display_name") or f.get("name") or ""
        task = name.rsplit(".", 1)[0] if name else ""
        sjob = st.get("job") or {}
        ver = raw.get("version") or {}
        return base_state(
            self, True, error or ("La stampante richiede attenzione" if pstate == "ATTENTION" else ""),
            state=state, task_name=task if state in ("RUNNING", "PAUSE", "PREPARE") or pstate in RESULT else "",
            task_id=f"{sjob.get('id') or job.get('id') or ''}:{task}",
            progress=int(sjob.get("progress") or job.get("progress") or 0),
            remaining_min=int((sjob.get("time_remaining") or job.get("time_remaining") or 0) // 60),
            nozzles=[nozzle(0, "T0", pr.get("temp_nozzle"), pr.get("target_nozzle"))],
            bed=pr.get("temp_bed"), bed_target=pr.get("target_bed"),
            speed_pct=int(pr.get("speed") or 100),
            fans={"part": None, "aux": None, "chamber": None},
            firmware=ver.get("firmware") or ver.get("server") or "", model=ver.get("hostname") or "Prusa",
            last_result=RESULT.get(pstate) if name else None)

    def info(self, raw: dict) -> dict:
        ver = raw.get("version") or {}
        return {"board": f"PrusaLink {ver.get('server', '')}".strip(), "firmware": ver.get("firmware") or ver.get("server"),
                "mode": "API " + str(ver.get("api", "")), "status": ((raw.get("status") or {}).get("printer") or {}).get("state", "").lower(),
                "tools": 1}

    def _job_id(self) -> int:
        jid = ((self.model().get("status") or {}).get("job") or {}).get("id")
        if not jid:
            raise HubError(key="Nessuna stampa in corso")
        return jid

    def pause(self):
        self.request("PUT", f"/api/v1/job/{self._job_id()}/pause")

    def resume(self):
        self.request("PUT", f"/api/v1/job/{self._job_id()}/resume")

    def stop(self):
        self.request("DELETE", f"/api/v1/job/{self._job_id()}")

    def _storage_path(self) -> str:
        if not self._storage:
            data = self.request("GET", "/api/v1/storage").json()
            usable = [s for s in data.get("storage_list") or [] if s.get("available", True) and not s.get("read_only")]
            if not usable:
                raise HubError(key="Nessuna memoria disponibile sulla stampante (inserisci una chiavetta USB)")
            self._storage = (usable[0].get("path") or "/usb").strip("/")
        return self._storage

    def start(self, local_path: str, name: str):
        with open(local_path, "rb") as fh:
            data = fh.read()
        path = f"/api/v1/files/{self._storage_path()}/{urllib.parse.quote(name)}"
        self.request("PUT", path, data=data, timeout=600,
                     headers={"Content-Type": "application/octet-stream", "Print-After-Upload": "?1", "Overwrite": "?1"})
