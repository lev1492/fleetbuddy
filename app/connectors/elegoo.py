"""Stampanti Elegoo con protocollo SDCP v3 ("Elegoo Link"): Centauri Carbon e resine recenti (Saturn 4 Ultra, Mars 5 Ultra…).

Ricerca:    UDP "M99999" alla porta 3000 → nome, modello, firmware e MainboardID
Stato:      WebSocket ws://IP:3030/websocket, richiesta Cmd 0 → messaggio sul topic sdcp/status/<MainboardID>
Comandi:    Cmd 128 avvio (con livellamento e timelapse), 129 pausa, 130 stop, 131 ripresa, 386 attivazione del video
Stampa:     caricamento a blocchi su http://IP:3030/uploadFile/upload, poi Cmd 128 con il file in /local/
Webcam:     flusso MJPEG indicato dalla stampante (Cmd 386); FleetBuddy ne estrae un fotogramma
Limiti:     SDCP non ha comandi documentati per temperature e velocità: si leggono ma non si modificano.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import uuid

import requests

from ..hub_client import HubError
from .base import DirectClient, base_state, bed, nozzle

# PrintInfo.Status (SDCP): 5 in pausa…, 6 in pausa, 7 arresto in corso, 8 fermata, 9 completata; 0 inattiva
PAUSED, STOPPING, STOPPED, DONE = {5, 6}, {7}, {8}, {9}
CHUNK = 1024 * 1024


class ElegooClient(DirectClient):
    kind = "elegoo"
    label = "Elegoo (SDCP)"
    caps = {"temperature": False, "speed": False, "filament": False, "bed_clean": False,
            "print_options": ["bed_leveling", "timelapse"]}

    def __init__(self, row: dict):
        super().__init__(row)
        from urllib.parse import urlsplit
        self.ip = urlsplit(self.base).hostname
        self.ws_port = int(os.environ.get("FLEETBUDDY_SDCP_PORT", 3030))     # sovrascrivibile solo per i test
        self.udp_port = int(os.environ.get("FLEETBUDDY_SDCP_UDP", 3000))
        self.mainboard = self.extra or ""
        self.info_data: dict = {}
        self._video = ""

    # ------------------------------------------------------------------ protocollo
    def discover(self):
        """Ricerca SDCP: la stampante risponde a "M99999" con i propri dati (tra cui MainboardID)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        try:
            s.sendto(b"M99999", (self.ip, self.udp_port))
            data, _ = s.recvfrom(4096)
            d = json.loads(data.decode("utf-8", "replace"))
            self.info_data = d.get("Data", d)
            self.mainboard = self.info_data.get("MainboardID") or self.mainboard
        except (OSError, ValueError):
            pass   # alcune reti bloccano l'UDP: si prosegue e il MainboardID arriva con il primo stato
        finally:
            s.close()

    def _exchange(self, cmd: int, data: dict | None = None, want_status=False, timeout=5.0) -> tuple[dict | None, dict | None]:
        import websocket
        try:
            ws = websocket.create_connection(f"ws://{self.ip}:{self.ws_port}/websocket", timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise HubError(key="Stampante non raggiungibile: {e}", params={"e": e}) from e
        rid = uuid.uuid4().hex
        req = {"Id": self.info_data.get("Id", ""), "Topic": f"sdcp/request/{self.mainboard}",
               "Data": {"Cmd": cmd, "Data": data or {}, "RequestID": rid, "MainboardID": self.mainboard,
                        "TimeStamp": int(time.time()), "From": 1}}
        response = status = None
        try:
            ws.send(json.dumps(req))
            end = time.time() + timeout
            while time.time() < end and (response is None or (want_status and status is None)):
                ws.settimeout(max(0.2, end - time.time()))
                try:
                    msg = json.loads(ws.recv())
                except Exception:  # noqa: BLE001 - timeout o messaggio non JSON
                    break
                topic = msg.get("Topic", "")
                if "/status/" in topic or "Status" in msg:
                    status = msg.get("Status") or status
                    self.mainboard = self.mainboard or msg.get("MainboardID", "")
                elif "/response/" in topic and (msg.get("Data") or {}).get("RequestID") == rid:
                    response = (msg.get("Data") or {}).get("Data") or {}
                elif "/attributes/" in topic:
                    self.info_data.update(msg.get("Attributes") or {})
        finally:
            ws.close()
        return response, status

    def _command(self, cmd: int, data: dict | None = None) -> dict:
        resp, _ = self._exchange(cmd, data)
        if resp is None:
            raise HubError(key="La stampante non ha risposto al comando")
        if resp.get("Ack", 0) != 0:
            raise HubError(key="Comando rifiutato dalla stampante (codice {c})", params={"c": resp.get("Ack")})
        return resp

    # ------------------------------------------------------------------ lettura
    def model(self) -> dict:
        if not self.info_data:
            self.discover()
        _, status = self._exchange(0, want_status=True)
        if status is None:
            raise HubError(key="La stampante non ha inviato il proprio stato")
        return {"info": self.info_data, "status": status}

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error, model="Elegoo")
        s, info = raw.get("status") or {}, raw.get("info") or {}
        pi = s.get("PrintInfo") or {}
        code = int(pi.get("Status") or 0)
        busy = 1 in (s.get("CurrentStatus") or [])
        if code in PAUSED:
            state = "PAUSE"
        elif busy or code in STOPPING:
            state = "RUNNING" if code not in (0,) or busy else "IDLE"
        else:
            state = "IDLE"
        fname = pi.get("Filename") or ""
        task = os.path.basename(fname).rsplit(".", 1)[0] if fname else ""
        total, cur = pi.get("TotalTicks") or 0, pi.get("CurrentTicks") or 0
        scale = 1000 if total > 7 * 86400 else 1          # alcuni firmware usano i millisecondi, altri i secondi
        layer, layers = pi.get("CurrentLayer") or 0, pi.get("TotalLayer") or 0
        progress = pi.get("Progress")
        if progress is None:
            progress = int(100 * layer / layers) if layers else 0
        fans = s.get("CurrentFanSpeed") or {}
        noz = [nozzle(0, "T0", s.get("TempOfNozzle"), s.get("TempTargetNozzle"))] if "TempOfNozzle" in s else []
        beds = [bed(0, "", s.get("TempOfHotbed"), s.get("TempTargetHotbed"))] if "TempOfHotbed" in s else []
        result = "finished" if code in DONE else "cancelled" if code in STOPPED else None
        return base_state(
            self, True, error, state=state, task_name=task if state != "IDLE" or result else "",
            task_id=f"{pi.get('TaskId') or ''}:{task}", progress=int(progress),
            remaining_min=int(max(0, total - cur) / scale // 60), layer=layer, total_layers=layers,
            nozzles=noz, beds=beds, chamber=s.get("TempOfBox"), chamber_target=s.get("TempTargetBox") or 0,
            speed_pct=int(pi.get("PrintSpeedPct") or 100),
            fans={"part": fans.get("ModelFan"), "aux": fans.get("AuxiliaryFan"), "chamber": fans.get("BoxFan")},
            firmware=info.get("FirmwareVersion") or "", model=info.get("MachineName") or "Elegoo",
            last_result=result if fname else None,
            caps={**self.caps, "chamber_heater": False, "resin": not noz})

    def info(self, raw: dict) -> dict:
        i = raw.get("info") or {}
        return {"board": f"{i.get('BrandName', 'Elegoo')} {i.get('MachineName', '')}".strip(), "firmware": i.get("FirmwareVersion"),
                "mode": f"SDCP {i.get('ProtocolVersion', '')}".strip(), "status": self.normalize(raw)["state"].lower(),
                "tools": 1 if "TempOfNozzle" in (raw.get("status") or {}) else 0}

    def snapshot(self) -> bytes | None:
        if self.snapshot_url:
            return super().snapshot()
        if not self._video:
            try:
                self._video = (self._command(386, {"Enable": 1}) or {}).get("VideoUrl", "")
            except HubError:
                return None
            if self._video and not self._video.startswith("http"):
                self._video = "http://" + self._video
        if not self._video:
            return None
        try:   # primo fotogramma JPEG del flusso MJPEG
            with self._s.get(self._video, stream=True, timeout=5) as r:
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
            self._video = ""
        return None

    def has_snapshot(self) -> bool:
        return True

    # ------------------------------------------------------------------ comandi
    def pause(self):
        self._command(129)

    def resume(self):
        self._command(131)

    def stop(self):
        self._command(130)

    def start(self, local_path: str, name: str, options: dict | None = None):
        opts = options or {}
        size = os.path.getsize(local_path)
        md5 = hashlib.md5(open(local_path, "rb").read()).hexdigest()
        up = str(uuid.uuid4())
        with open(local_path, "rb") as fh:
            offset = 0
            while offset < size or size == 0:
                part = fh.read(CHUNK)
                try:
                    r = self._s.post(f"http://{self.ip}:{self.ws_port}/uploadFile/upload", timeout=120,
                                     data={"S-File-MD5": md5, "Check": "1", "Offset": str(offset), "Uuid": up, "TotalSize": str(size)},
                                     files={"File": (name, part, "application/octet-stream")})
                    ok = r.status_code == 200 and (r.json().get("success") or r.json().get("code") == "000000")
                except (requests.RequestException, ValueError) as e:
                    raise HubError(key="Caricamento del file sulla stampante non riuscito") from e
                if not ok:
                    raise HubError(key="Caricamento del file sulla stampante non riuscito")
                offset += len(part)
                if size == 0:
                    break
        self._command(128, {"Filename": f"/local/{name}", "StartLayer": 0,
                            "Calibration_switch": 1 if opts.get("bed_leveling", True) else 0,
                            "PrintPlatformType": 0, "Tlp_Switch": 1 if opts.get("timelapse") else 0})
