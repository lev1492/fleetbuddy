"""OctoPrint (API REST, intestazione X-Api-Key).

Stato:      GET /api/printer (temperature e stato), GET /api/job (file, avanzamento, tempo rimanente)
Comandi:    POST /api/job {pause|cancel}, POST /api/printer/tool|bed|chamber (temperature),
            POST /api/printer/printhead {feedrate} (velocità)
Stampa:     POST /api/files/local (multipart, select + print)
Foto:       snapshotUrl della webcam letto da GET /api/settings (se la chiave ha i permessi), oppure indirizzo manuale
"""
from __future__ import annotations

from ..hub_client import HubError
import urllib.parse

from .base import DirectClient, base_state, mm_to_grams, nozzle, plan_from_meta


class OctoPrintClient(DirectClient):
    kind = "octoprint"
    web_default = "base"                      # interfaccia web
    label = "OctoPrint"
    caps = {"temperature": True, "speed": True, "filament": False, "bed_clean": False}

    def __init__(self, row: dict):
        super().__init__(row)
        self._s.headers["X-Api-Key"] = self.api_key
        self._version: dict = {}
        self._speed = 100   # OctoPrint non comunica il fattore attuale: l'ultimo impostato da qui

    def model(self) -> dict:
        if not self._version:
            self._version = self.request("GET", "/api/version").json()
            try:
                cam = (self.request("GET", "/api/settings").json().get("webcam") or {})
                if cam.get("webcamEnabled", True) and cam.get("snapshotUrl"):
                    self.auto_snapshot_url = cam["snapshotUrl"]
            except HubError:
                pass
        printer = self.request("GET", "/api/printer", ok=(200, 409)).json()
        job = self.request("GET", "/api/job").json()
        return {"version": self._version, "printer": printer, "job": job}

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error)
        pr, job = raw.get("printer") or {}, raw.get("job") or {}
        flags = ((pr.get("state") or {}).get("flags") or {})
        if not flags:                                   # 409: OctoPrint non collegato alla stampante
            st = base_state(self, False, error or "OctoPrint non è collegato alla stampante")
            st["firmware"] = (raw.get("version") or {}).get("server", "")
            return st
        if flags.get("error") or flags.get("closedOrError"):
            state = "FAILED"
        elif flags.get("printing") or flags.get("cancelling"):
            state = "RUNNING"
        elif flags.get("paused") or flags.get("pausing"):
            state = "PAUSE"
        else:
            state = "IDLE"
        temps = pr.get("temperature") or {}
        tools = sorted(k for k in temps if k.startswith("tool"))
        noz = [nozzle(int(k[4:]), f"T{k[4:]}", (temps[k] or {}).get("actual"), (temps[k] or {}).get("target")) for k in tools]
        f = (job.get("job") or {}).get("file") or {}
        prog = job.get("progress") or {}
        done = prog.get("completion") or 0
        name = f.get("name") or ""
        task = name.rsplit(".", 1)[0] if name else ""
        active = state in ("RUNNING", "PAUSE")
        return base_state(
            self, True, error, state=state, task_name=task if (active or done) else "",
            progress=int(done), remaining_min=int((prog.get("printTimeLeft") or 0) // 60), elapsed_s=prog.get("printTime") or 0,
            nozzles=noz, bed=(temps.get("bed") or {}).get("actual"), bed_target=(temps.get("bed") or {}).get("target"),
            chamber=(temps.get("chamber") or {}).get("actual"), chamber_target=(temps.get("chamber") or {}).get("target") or 0,
            firmware=(raw.get("version") or {}).get("server", ""), model="OctoPrint", speed_pct=self._speed,
            # OctoPrint non ha uno stato "terminata": il lavoro resta con l'avanzamento al 100 % se completato
            last_result=("finished" if done >= 99.9 else "cancelled") if name and state == "IDLE" else None)

    def info(self, raw: dict) -> dict:
        st = self.normalize(raw)
        return {"board": "OctoPrint", "firmware": st["firmware"], "mode": None,
                "status": st["state"].lower(), "tools": len(st["nozzles"])}

    def job_plan(self, raw: dict | None) -> list[dict] | None:
        jj = (((raw or {}).get("job") or {}).get("job") or {})
        f = jj.get("file") or {}
        path, origin = f.get("path") or f.get("name"), f.get("origin") or "local"
        if path and origin == "local":
            plan = plan_from_meta(self.fetch_gcode_meta(f"/downloads/files/local/{urllib.parse.quote(path)}", path))
            if plan:
                return plan
        out = []
        for k, v in sorted((jj.get("filament") or {}).items()):
            if not isinstance(v, dict) or not (v.get("length") or v.get("volume")):
                continue
            grams = v["volume"] * 1.24 if v.get("volume") else mm_to_grams(v["length"])
            out.append({"type": "?", "brand": "", "color": "", "tray_info_idx": "", "grams": round(grams, 2),
                        "meters": round((v.get("length") or 0) / 1000, 2), "ams_id": None, "slot_id": int(k[4:] or 0) if k.startswith("tool") else 0})
        return out or None

    def pause(self):
        self.request("POST", "/api/job", json={"command": "pause", "action": "pause"})

    def resume(self):
        self.request("POST", "/api/job", json={"command": "pause", "action": "resume"})

    def stop(self):
        self.request("POST", "/api/job", json={"command": "cancel"})

    def start(self, local_path: str, name: str, options: dict | None = None):
        with open(local_path, "rb") as fh:
            self.request("POST", "/api/files/local", files={"file": (name, fh, "application/octet-stream")},
                         data={"select": "true", "print": "true"}, timeout=600)

    def set_temperature(self, target: str, value: float):
        v = max(0, min(400, float(value)))
        if target == "bed":
            self.request("POST", "/api/printer/bed", json={"command": "target", "target": v})
        elif target == "chamber":
            self.request("POST", "/api/printer/chamber", json={"command": "target", "target": v})
        elif target.startswith("tool:"):
            self.request("POST", "/api/printer/tool", json={"command": "target", "targets": {f"tool{int(target[5:])}": v}})
        else:
            raise HubError(key="Riscaldatore non valido")

    def set_speed(self, pct: int):
        self._speed = max(10, min(300, int(pct)))
        self.request("POST", "/api/printer/printhead", json={"command": "feedrate", "factor": self._speed})
