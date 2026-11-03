#!/usr/bin/env python3
"""Fleet Hub simulato per sviluppare FleetBuddy senza hardware.

Espone le principali API in HTTPS con mTLS obbligatorio, usando certificati auto-generati:
  bash tools/make_mock_certs.sh          # crea certs/mock/*
  python -m tools.mock_hub               # ascolta su 0.0.0.0:8888
Poi in config.toml: host="127.0.0.1", client_cert="certs/mock/client.crt",
client_key="certs/mock/client.key", server_ca="certs/mock/ca.crt", password="Mock1234!".
"""
import asyncio
import base64
import os
import hashlib
import json
import random
import secrets
import ssl
import struct
import time
import zlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI()
USER, PWD = "admin", "Mock1234!"
TICKETS: dict[int, dict] = {}
TOKENS: set[str] = set()
CACHE: dict[str, float] = {}


def tray(i, typ, col, rfid=False):
    t = {"id": str(i), "tray_type": typ, "tray_color": col, "tray_info_idx": "GFA00" if typ == "PLA" else "GFG00",
         "tag_uid": "A1B2C3D4E5F60708" if rfid else "0000000000000000", "tray_sub_brands": "PLA Basic" if rfid else "",
         "remain": 72 if rfid else -1}
    if rfid:
        t["tray_weight"] = "1000"
    return t


PRINTERS = {
    "01P00A000000001": {"model": "P1S", "name": "P1S officina", "state": "IDLE", "pct": 0, "t0": 0, "dur": 240,
                        "ams": [tray(0, "PLA", "F98C36FF", True), tray(1, "PLA", "161616FF"), tray(2, "PETG", "FFFFFFFF"), {"id": "3"}]},
    "0948AB000000002": {"model": "H2D", "name": "H2D grande", "state": "IDLE", "pct": 0, "t0": 0, "dur": 300,
                        "ams": [tray(0, "PLA", "2850E0FF"), tray(1, "PLA", "E02828FF"), tray(2, "PLA", "FFFFFFFF"), tray(3, "PETG", "161616FF")]},
}


def ok(**kw):
    return {"code": 0, "message": "", **kw}


def err(code, msg, status=400):
    return JSONResponse({"code": code, "message": msg}, status_code=status)


def authed(req: Request):
    return req.headers.get("authorization", "")[7:] in TOKENS


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


@app.get("/v1/hub/captain")
def captain():
    return {"name": "Hub", "hub_sn": "34K000MOCK", "server_id": "34K000MOCK", "activate_state": "Activate",
            "activate_uid": "123", "max_bind_num": 50, "version": "01.00.02.06",
            "internal": {"available": 39, "used": 1}, "udisk": {"conn_state": 0, "available": 0, "used": 0}}


FAILS: list[float] = []
LOCK_UNTIL = [0.0]


def locked():
    return time.time() < LOCK_UNTIL[0]


@app.post("/v1/hub/login/local/tickets")
async def ticket(req: Request):
    if locked():
        LOCK_UNTIL[0] = time.time() + 600   # ogni tentativo durante il blocco lo prolunga
        return err(1026, "login too frequent, please try again after 10 minutes", 429)
    tid = random.randint(1, 10**9)
    t = {"challenge": secrets.token_urlsafe(6), "salt": secrets.token_urlsafe(10)}
    TICKETS[tid] = t
    return ok(ticket_id=tid, ticket=base64.b64encode(json.dumps(t).encode()).decode())


@app.post("/v1/hub/login")
async def login(req: Request):
    b = await req.json()
    t = TICKETS.pop(b.get("ticket_id"), None)
    if not t or b.get("user_name") != USER or b.get("password") != h(h(PWD + t["salt"]) + t["challenge"]):
        FAILS.append(time.time())
        if len([x for x in FAILS if time.time() - x < 600]) >= 5:
            LOCK_UNTIL[0] = time.time() + 600
        return err(1023, "wrong user name or password")
    tok = secrets.token_hex(16)
    TOKENS.add(tok)
    return ok(token=tok)


def advance():
    for p in PRINTERS.values():
        if p["state"] in ("RUNNING", "PREPARE"):
            old = p["pct"]
            p["pct"] = min(100, int(100 * (time.time() - p["t0"]) / p["dur"]))
            # la bobina RFID in uso cala del 3 % ogni 10 % di avanzamento (≈ 30 g su 1 kg)
            for t in p["ams"]:
                if isinstance(t.get("remain"), int) and t["remain"] >= 0:
                    t["remain"] -= 3 * (p["pct"] // 10 - old // 10)
            p["state"] = "RUNNING" if p["pct"] > 2 else "PREPARE"
            if p["pct"] >= 100:
                p["state"] = "FINISH"


def device(sn, p):
    return {
        "dev_sn": sn, "dev_model": p["model"], "dev_ip": "10.0.0." + sn[-1], "name": p["name"], "online": True,
        "mqtt_status": 2, "liveview_time": "2026-11-02T10:00:00Z",
        "upgrade_info": {"current_ver": "01.08.00.00", "upgrade_state": "IDLE"},
        "report_status": {
            "gcode_state": p["state"], "mc_percent": p["pct"], "mc_remaining_time": int(p["dur"] * (100 - p["pct"]) / 6000),
            "layer_num": p["pct"] * 2, "total_layer_num": 200, "subtask_name": p.get("task", ""),
            "task_id": str(p.get("task_id", 0)), "subtask_id": str(p.get("task_id", 0)),
            "nozzle_temper": 220.0 if p["state"] == "RUNNING" else 28.5, "nozzle_target_temper": 220 if p["state"] == "RUNNING" else 0,
            "bed_temper": 60.0 if p["state"] == "RUNNING" else 25.0, "bed_target_temper": 60 if p["state"] == "RUNNING" else 0,
            "spd_lvl": 2, "wifi_signal": "-55dBm", "print_error": 0, "ams_status": 0, "nozzle_diameter": "0.4",
            "hms": [{"attr": 50336256, "code": 131073}] if p["state"] == "FINISH" and p["model"] == "P1S" else [],
            "ams": {"ams_exist_bits": "1", "ams": [{"id": "0", "humidity": "3", "tray": p["ams"]}]},
            "vt_tray": {"id": "255", "tray_type": "", "tray_color": "00000000"} if p["model"] != "H2D" else {},
            "vir_slot": [{"id": "255", "tray_type": "PLA", "tray_color": "00AA55FF"}, {"id": "254", "tray_type": ""}] if p["model"] == "H2D" else [],
            "device": {"extruder": {"info": [{"id": 0, "temp": (220 << 16) | 219}, {"id": 1, "temp": 35}]},
                       "ctc": {"info": {"temp": (45 << 16) | 38}}} if p["model"] == "H2D" else {},
        },
    }


@app.post("/mock/start/{sn}")
def mock_external_print(sn: str, dur: int = 20):
    """Solo per i test: simula una stampa avviata da Bambu Studio (fuori da FleetBuddy)."""
    PRINTERS[sn].update(state="PREPARE", pct=0, t0=time.time(), dur=dur, task="Da Bambu Studio",
                        task_id=random.randint(1, 10**6))
    return ok()


@app.get("/v1/hub/devices")
def devices(req: Request):
    if not authed(req):
        return err(6, "invalid token", 401)
    advance()
    return ok(devices=[device(sn, p) for sn, p in PRINTERS.items()])


@app.post("/v1/hub/devices/{sn}/opt")
async def opt(sn: str, req: Request):
    if not authed(req):
        return err(6, "invalid token", 401)
    b = await req.json()
    p = PRINTERS[sn]
    o = b["opt"]
    if o == "pause":
        p["state"] = "PAUSE"
    elif o == "resume":
        p["state"] = "RUNNING"
    elif o == "stop":
        p["state"] = "FAILED"
    elif o == "bed_clean":
        p["state"], p["pct"] = "IDLE", 0
    elif o == "ams_filament_setting":
        s = b[o]
        p["ams"][s["slot_id"]] = tray(s["slot_id"], s["tray_type"], s["tray_color"])
    return ok()


@app.put("/v1/hub/devices/print")
async def do_print(req: Request):
    if not authed(req):
        return err(6, "invalid token", 401)
    form = await req.form()
    cmd = json.loads(form["print_cmd"])
    if "file" in form:
        data = await form["file"].read()
        CACHE[hashlib.md5(data).hexdigest()] = time.time()
    elif form.get("file_hash") not in CACHE:
        return err(12, "3mf not exist")
    for sn in form.getlist("dev_sns"):
        p = PRINTERS[sn]
        if p["state"] != "IDLE":
            return err(1051, "device busy")
        p.update(state="PREPARE", pct=0, t0=time.time(), task=cmd["task_name"], task_id=random.randint(1, 10**6))
    print("print_cmd:", cmd)
    await asyncio.sleep(float(os.environ.get("MOCK_PRINT_DELAY", "0")))
    return ok()


@app.post("/debug/start")
def debug_start(sn: str, task: str):
    """Simula una stampa avviata da Bambu Studio (non da FleetBuddy)."""
    PRINTERS[sn].update(state="PREPARE", pct=0, t0=time.time(), task=task, task_id=random.randint(1, 10**6))
    return ok()


@app.post("/debug/finish")
def debug_finish(sn: str):
    PRINTERS[sn].update(state="FINISH", pct=100, t0=time.time() - PRINTERS[sn]["dur"])
    return ok()


@app.get("/v1/hub/file/info")
def file_info(req: Request, file_path: str = ""):
    if not authed(req):
        return err(6, "invalid token", 401)
    if file_path:
        return Response(_png_as_fake_jpeg(), media_type="application/octet-stream")
    return ok(file_infos=[{"file_hash": k, "last_use_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v))} for k, v in CACHE.items()])


def _png_as_fake_jpeg():
    # immagine 2x2 grigia (PNG): i browser la mostrano anche con Content-Type jpeg
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00\x80\x80\x80\x80\x80\x80\x00\x80\x80\x80\x80\x80\x80"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@app.get("/v1/hub/hms")
def hms(dev_model: str, error_code: str):
    return ok(description=f"(simulato) Descrizione HMS {error_code} per {dev_model}")


@app.get("/v1/hub/device_error")
def dev_err(dev_model: str, error_code: str):
    return ok(description=f"(simulato) Errore {error_code}")


@app.get("/v1/hub/captain/ntp")
def ntp():
    return {"ntp_switch": "close", "time_zone": "UTC+01:00", "date": time.strftime("%Y-%m-%d %H:%M:%S"), "ntp_url": ""}


@app.put("/v1/hub/captain/ntp")
def ntp_set():
    return ok()


@app.get("/v1/hub/local_printers")
def local_printers():
    return ok(printers=[{"location": "10.0.0.77", "usn": "01S00C000000077", "model": "A1", "name": "A1 nuova",
                         "connect": "cloud", "bind": "free", "farm": True}])


@app.get("/v1/hub/scan_printers")
def scan(prefix: str, begin: int, end: int):
    return local_printers()


@app.put("/v1/hub/bind")
def bind():
    return ok()


@app.delete("/v1/hub/bind")
async def unbind(req: Request):
    b = await req.json()
    return ok(unbind_results=[{"dev_id": d, "code": 0, "message": ""} for d in b["dev_ids"]])


@app.get("/v1/hub/file/firmwares")
def fws():
    return ok(firmwares=[])


if __name__ == "__main__":
    d = ROOT / "certs" / "mock"
    uvicorn.run(app, host="0.0.0.0", port=8888, ssl_certfile=str(d / "server.crt"), ssl_keyfile=str(d / "server.key"),
                ssl_ca_certs=str(d / "ca.crt"), ssl_cert_reqs=ssl.CERT_REQUIRED)
