"""Repetier-Server (API REST su porta 3344, chiave API in X-Api-Key).

Una installazione di Repetier-Server può gestire più stampanti: si indica lo "slug" della stampante
(campo facoltativo; se vuoto si usa la prima).
Stato:      GET /printer/list (lavoro, avanzamento, tempi), GET /printer/api/{slug}?a=stateList (temperature, ventole, velocità)
Comandi:    a=send con "@pause" (pausa), a=continueJob, a=stopJob, a=send con G-code (temperature M104/M140/M141, velocità M220)
Stampa:     POST /printer/job/{slug} (multipart, a=upload): il lavoro parte appena la stampante è libera
"""
from __future__ import annotations

import json

from ..hub_client import HubError
from .base import DirectClient, base_state, nozzle


class RepetierClient(DirectClient):
    kind = "repetier"
    web_default = "base"                      # interfaccia web
    label = "Repetier-Server"
    default_port = 3344
    caps = {"temperature": True, "speed": True, "filament": False, "bed_clean": False}

    def __init__(self, row: dict):
        super().__init__(row)
        self._s.headers["X-Api-Key"] = self.api_key
        self.slug = self.extra
        self._server: dict = {}

    def _printer(self, plist: list[dict]) -> dict:
        if not self.slug:
            if not plist:
                raise HubError(key="Repetier-Server non ha stampanti configurate")
            self.slug = plist[0].get("slug", "")
        p = next((x for x in plist if x.get("slug") == self.slug), None)
        if not p:
            raise HubError(key="Stampante {s} non trovata in Repetier-Server", params={"s": self.slug})
        return p

    def model(self) -> dict:
        if not self._server:
            self._server = self.request("GET", "/printer/info").json()
        plist = self.request("GET", "/printer/list").json()
        plist = plist.get("data", plist) if isinstance(plist, dict) else plist
        p = self._printer(plist)
        state = {}
        if p.get("online"):
            state = (self.request("GET", f"/printer/api/{self.slug}", params={"a": "stateList"}).json() or {}).get(self.slug, {})
        return {"server": self._server, "printer": p, "state": state}

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error)
        p, s = raw.get("printer") or {}, raw.get("state") or {}
        if not p.get("online"):
            st = base_state(self, False, error or "Repetier-Server non è collegato alla stampante")
            st["firmware"] = (raw.get("server") or {}).get("version", "")
            return st
        job = p.get("job") if p.get("job") not in (None, "", "none") else ""
        state = ("PAUSE" if p.get("paused") else "RUNNING") if job else "IDLE"
        total = p.get("printTime") or 0
        elapsed = p.get("printedTimeComp") or 0
        noz = [nozzle(i, f"T{i}", e.get("tempRead"), e.get("tempSet")) for i, e in enumerate(s.get("extruder") or [])]
        bed = (s.get("heatedBeds") or [{}])[0] if s.get("heatedBeds") else {}
        ch = (s.get("heatedChambers") or [{}])[0] if s.get("heatedChambers") else {}
        fans = s.get("fans") or []
        part = round(100 * fans[0].get("voltage", 0) / 255) if fans and fans[0].get("on") else (0 if fans else None)
        return base_state(
            self, True, error, state=state, task_name=job.rsplit(".", 1)[0] if job else "",
            task_id=f"{p.get('jobid') or ''}:{job}", progress=int(p.get("done") or 0),
            remaining_min=int(max(0, total - elapsed) // 60), elapsed_s=elapsed, layer=s.get("layer") or 0,
            nozzles=noz, bed=bed.get("tempRead"), bed_target=bed.get("tempSet"),
            chamber=ch.get("tempRead"), chamber_target=ch.get("tempSet") or 0,
            speed_pct=int(s.get("speedMultiply") or 100), fans={"part": part, "aux": None, "chamber": None},
            firmware=(raw.get("server") or {}).get("version", ""), model=p.get("name") or "Repetier",
            name=self.name)

    def info(self, raw: dict) -> dict:
        st = self.normalize(raw)
        return {"board": f"Repetier-Server · {(raw.get('printer') or {}).get('name', self.slug)}", "firmware": st["firmware"],
                "mode": self.slug, "status": st["state"].lower(), "tools": len(st["nozzles"])}

    def _api(self, action: str, data: dict | None = None):
        params = {"a": action}
        if data is not None:
            params["data"] = json.dumps(data)
        self.request("GET", f"/printer/api/{self.slug}", params=params)

    def _send(self, cmd: str):
        self._api("send", {"cmd": cmd})

    def pause(self):
        self._send("@pause")

    def resume(self):
        self._api("continueJob", {})

    def stop(self):
        self._api("stopJob", {})

    def start(self, local_path: str, name: str, options: dict | None = None):
        if not self.slug:
            self.model()
        with open(local_path, "rb") as fh:
            self.request("POST", f"/printer/job/{self.slug}", data={"a": "upload"},
                         files={"filename": (name, fh, "application/octet-stream")}, timeout=600)

    def set_temperature(self, target: str, value: float):
        v = max(0, min(400, float(value)))
        if target == "bed":
            self._send(f"M140 S{v:g}")
        elif target == "chamber":
            self._send(f"M141 S{v:g}")
        elif target.startswith("tool:"):
            self._send(f"M104 T{int(target[5:])} S{v:g}")
        else:
            raise HubError(key="Riscaldatore non valido")

    def set_speed(self, pct: int):
        self._send(f"M220 S{max(10, min(300, int(pct)))}")
