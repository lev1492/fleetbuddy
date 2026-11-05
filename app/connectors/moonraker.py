"""Klipper tramite Moonraker (API REST, chiave API facoltativa in X-Api-Key).

Stato:      GET /printer/info (stato di Klipper), GET /printer/objects/query (print_stats, virtual_sdcard,
            estrusori, piano, camera, ventola, gcode_move)
Comandi:    POST /printer/print/pause|resume|cancel, POST /printer/gcode/script (temperature, velocità)
Stampa:     POST /server/files/upload (multipart, print=true)
Foto:       snapshot_url della prima webcam configurata (GET /server/webcams/list), oppure indirizzo manuale
Vale anche per stampanti con Klipper di serie che espongono Moonraker (es. Creality K1 con root, Elegoo Neptune 4, Sovol, Qidi).
"""
from __future__ import annotations

import urllib.parse

from ..hub_client import HubError
from .base import DirectClient, base_state, nozzle

STATE = {"printing": "RUNNING", "paused": "PAUSE", "standby": "IDLE", "complete": "IDLE", "cancelled": "IDLE", "error": "IDLE"}
RESULT = {"complete": "finished", "cancelled": "cancelled", "error": "failed"}


class MoonrakerClient(DirectClient):
    kind = "moonraker"
    label = "Klipper (Moonraker)"
    default_port = None
    caps = {"temperature": True, "speed": True, "filament": False, "bed_clean": False}

    def __init__(self, row: dict):
        super().__init__(row)
        if self.api_key:
            self._s.headers["X-Api-Key"] = self.api_key
        self._objects: list[str] | None = None
        self._chamber = ""            # oggetto Klipper della camera (heater_generic / temperature_sensor)

    def _discover(self):
        objs = self.request("GET", "/printer/objects/list").json()["result"]["objects"]
        self._objects = objs
        for o in objs:
            if "chamber" in o.lower() and o.split(" ", 1)[0] in ("heater_generic", "temperature_sensor", "temperature_fan"):
                self._chamber = o
                break
        try:
            cams = self.request("GET", "/server/webcams/list").json()["result"]["webcams"]
            if cams and cams[0].get("snapshot_url"):
                self.auto_snapshot_url = cams[0]["snapshot_url"]
        except (HubError, KeyError, ValueError):
            pass

    def model(self) -> dict:
        info = self.request("GET", "/printer/info").json()["result"]
        if info.get("state") != "ready":
            return {"info": info, "status": {}}
        if self._objects is None:
            self._discover()
        want = ["print_stats", "virtual_sdcard", "display_status", "heater_bed", "gcode_move", "fan"]
        want += [o for o in self._objects if o == "extruder" or (o.startswith("extruder") and o[8:].isdigit())]
        if self._chamber:
            want.append(self._chamber)
        q = "&".join(urllib.parse.quote(o) for o in want)
        status = self.request("GET", f"/printer/objects/query?{q}").json()["result"]["status"]
        return {"info": info, "status": status}

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error)
        info, s = raw.get("info") or {}, raw.get("status") or {}
        version = info.get("software_version", "")
        if info.get("state") != "ready":           # Klipper in errore o spento: Moonraker risponde comunque
            return base_state(self, True, error or info.get("state_message", ""), state="FAILED",
                              firmware=version, model="Klipper")
        ps = s.get("print_stats") or {}
        kstate = ps.get("state", "standby")
        state = STATE.get(kstate, "IDLE")
        fname = ps.get("filename") or ""
        task = fname.rsplit("/", 1)[-1].rsplit(".", 1)[0] if fname else ""
        progress = (s.get("virtual_sdcard") or {}).get("progress") or (s.get("display_status") or {}).get("progress") or 0
        dur = ps.get("print_duration") or 0
        remaining = (dur / progress - dur) if progress > 0.01 else 0
        exts = sorted((k for k in s if k == "extruder" or (k.startswith("extruder") and k[8:].isdigit())),
                      key=lambda k: int(k[8:] or 0))
        noz = [nozzle(i, f"T{i}", (s[k] or {}).get("temperature"), (s[k] or {}).get("target")) for i, k in enumerate(exts)]
        ch = s.get(self._chamber) or {} if self._chamber else {}
        layer = ((ps.get("info") or {}).get("current_layer")) or 0
        total = ((ps.get("info") or {}).get("total_layer")) or 0
        fan = (s.get("fan") or {}).get("speed")
        return base_state(
            self, True, error, state=state, task_name=task if state in ("RUNNING", "PAUSE") or kstate in RESULT else "",
            progress=int(progress * 100), remaining_min=int(remaining // 60), layer=layer, total_layers=total,
            nozzles=noz, bed=(s.get("heater_bed") or {}).get("temperature"), bed_target=(s.get("heater_bed") or {}).get("target"),
            chamber=ch.get("temperature"), chamber_target=ch.get("target") or 0,
            speed_pct=round(100 * ((s.get("gcode_move") or {}).get("speed_factor") or 1)),
            fans={"part": round(fan * 100) if fan is not None else None, "aux": None, "chamber": None},
            firmware=version, model="Klipper", last_result=RESULT.get(kstate) if fname else None,
            caps={**self.caps, "chamber_heater": self._chamber.startswith("heater_generic")})

    def info(self, raw: dict) -> dict:
        st = self.normalize(raw)
        return {"board": f"Klipper ({(raw.get('info') or {}).get('hostname', '')})", "firmware": st["firmware"],
                "mode": None, "status": (raw.get("info") or {}).get("state"), "tools": len(st["nozzles"])}

    def _gcode(self, script: str):
        self.request("POST", "/printer/gcode/script", params={"script": script}, timeout=30)

    def pause(self):
        self.request("POST", "/printer/print/pause")

    def resume(self):
        self.request("POST", "/printer/print/resume")

    def stop(self):
        self.request("POST", "/printer/print/cancel")

    def start(self, local_path: str, name: str):
        with open(local_path, "rb") as fh:
            self.request("POST", "/server/files/upload", files={"file": (name, fh, "application/octet-stream")},
                         data={"root": "gcodes", "print": "true"}, timeout=600)

    def set_temperature(self, target: str, value: float):
        v = max(0, min(400, float(value)))
        if target == "bed":
            heater = "heater_bed"
        elif target == "chamber":
            if not self._chamber.startswith("heater_generic"):
                raise HubError(key="Questa stampante non ha un riscaldatore della camera")
            heater = self._chamber.split(" ", 1)[1]
        elif target.startswith("tool:"):
            i = int(target[5:])
            heater = "extruder" if i == 0 else f"extruder{i}"
        else:
            raise HubError(key="Riscaldatore non valido")
        self._gcode(f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={v:g}")

    def set_speed(self, pct: int):
        self._gcode(f"M220 S{max(10, min(300, int(pct)))}")
