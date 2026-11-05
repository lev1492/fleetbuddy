#!/usr/bin/env python3
"""Scheda Duet / RepRapFirmware 3 simulata, per provare FleetBuddy senza stampante.

  python -m tools.mock_duet                 # modalità autonoma (API rr_) su :8090, password "reprap"
  python -m tools.mock_duet --sbc --port 8091 --password segreta   # modalità SBC (API /machine)
"""
import argparse
import re
import time
import urllib.parse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI()
CFG = {"password": "reprap", "sbc": False, "rrf37": False}


def _thumb() -> str:
    import base64, sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from app.imgutil import rgba_to_qoi
    px = bytearray()
    for y in range(8):
        for x in range(8):
            px += bytes((220, 120 + x * 10, 40 + y * 20, 255))
    return base64.b64encode(rgba_to_qoi(8, 8, px)).decode()


THUMB = _thumb()
SESSIONS: set[str] = set()
FILES: dict[str, int] = {}
M = {"status": "idle", "file": None, "t0": 0, "dur": 40, "paused_pos": None, "speed": 1.0, "last": None,
     "temps": [[22.0, 0], [21.0, 0], [23.0, 0], [22.5, 0]]}   # T0, letto, camera, T1


def advance():
    for h in M["temps"]:                                     # le temperature inseguono l'obiettivo
        h[0] += (max(h[1], 21) - h[0]) * 0.5
    if M["status"] == "processing" and time.time() - M["t0"] >= M["dur"]:
        M.update(status="idle", last=M["file"], file=None, cancelled=False)


def model() -> dict:
    advance()
    pos = 0
    if M["file"]:
        frac = (M["paused_pos"] if M["status"] == "paused" else min(1, (time.time() - M["t0"]) / M["dur"]))
        pos = int(FILES.get(M["file"], 1000) * frac)
    heat = [{"current": round(t[0], 1), "active": t[1], "state": "active" if t[1] else "off"} for t in M["temps"]]
    if CFG["rrf37"]:   # come sulla Elemento: sensore di T1 scollegato
        heat[3] = {"current": 2000, "active": 0, "state": "fault"}
    return {
        "boards": [{"name": "Duet 3 MB6HC", "shortName": "MB6HC", "firmwareVersion": "3.5.1"}],
        "job": {"file": {"fileName": M["file"], "size": FILES.get(M["file"], 0), "numLayers": 120, "printTime": M["dur"],
                         "thumbnails": [{"format": "qoi", "width": 8, "height": 8, "offset": 100, "data": THUMB if CFG["sbc"] else None}]} if M["file"] else {},
                "filePosition": pos, "layer": int(120 * pos / max(1, FILES.get(M["file"], 1))) if M["file"] else 0,
                "timesLeft": {"slicer": max(0, int(M["dur"] - (time.time() - M["t0"]))) if M["file"] else None},
                "lastFileName": M["last"], "lastFileCancelled": bool(M.get("cancelled")), "lastFileAborted": False},
        "heat": ({"heaters": heat, "bedHeaterMapping": [[1]], "chamberHeaterMapping": [[2]]} if CFG["rrf37"]
                 else {"heaters": heat, "bedHeaters": [1, -1], "chamberHeaters": [2]}),
        "tools": [{"number": 0, "name": "T0", "heaters": [0], "fans": [0]}, {"number": 1, "name": "T1", "heaters": [3], "fans": [0]}],
        "fans": [{"actualValue": 0.6 if M["status"] == "processing" else 0.0}],
        "move": {"speedFactor": M["speed"]},
        "network": {"name": "Elemento-sim", "interfaces": [{"state": "active", "rssi": -48, "actualIP": "127.0.0.1"}]},
        "global": {"fxToolNozzle": [0.6, 0.8], "fxNozzleTypes": ["Normale", "HS"], "fxToolNozzleType": [0, 1],
                   "fxNozzleTypeAbrasive": [False, True], "fxToolHotSeconds": [49737, 50915], "fxPauseDetail": ""},
        "sensors": {"filamentMonitors": [{"enableMode": 1, "filamentPresent": True, "status": "ok"},
                                         {"enableMode": 1, "filamentPresent": False, "status": "noFilament"}]},
        "state": {"status": M["status"], "currentTool": 0, "displayMessage": "Printing..." if M["file"] else ""},
    }


def run(code: str):
    code = code.strip()
    if m := re.match(r'M32\s+"?([^"]+)"?', code):
        M.update(status="processing", file=m.group(1), t0=time.time(), paused_pos=None)
    elif code == "M25" and M["status"] == "processing":
        M.update(status="paused", paused_pos=min(1, (time.time() - M["t0"]) / M["dur"]))
    elif code == "M24" and M["status"] == "paused":
        M.update(status="processing", t0=time.time() - M["paused_pos"] * M["dur"])
    elif code == "M0" and M["status"] == "paused":
        M.update(status="idle", last=M["file"], file=None, cancelled=True)
    elif m := re.match(r"G10 P(\d+) S([\d.]+)", code):
        M["temps"][0 if m.group(1) == "0" else 3][1] = float(m.group(2))
    elif m := re.match(r"M140 S([\d.]+)", code):
        M["temps"][1][1] = float(m.group(1))
    elif m := re.match(r"M141 S([\d.]+)", code):
        M["temps"][2][1] = float(m.group(1))
    elif m := re.match(r"M220 S(\d+)", code):
        M["speed"] = int(m.group(1)) / 100
    print("G-code:", code)


def authed(req: Request) -> bool:
    return not CFG["password"] or req.headers.get("x-session-key") in SESSIONS


# ------------------------------------------------------------ modalità autonoma (rr_)
@app.get("/rr_connect")
def rr_connect(password: str = ""):
    if CFG["sbc"]:
        return PlainTextResponse("Not found", 404)
    if CFG["password"] and password != CFG["password"]:
        return {"err": 1}
    key = str(len(SESSIONS) + 1000)
    SESSIONS.add(key)
    return {"err": 0, "sessionTimeout": 8000, "boardType": "mb6hc", "apiLevel": 1, "sessionKey": key}


@app.get("/rr_model")
def rr_model(req: Request):
    if CFG["sbc"]:
        return PlainTextResponse("Not found", 404)
    if not authed(req):
        return JSONResponse({"err": 1}, 401)
    return {"key": "", "flags": "d99vno", "result": model()}


@app.get("/rr_thumbnail")
def rr_thumbnail(name: str, offset: int):
    return {"fileName": name, "offset": offset, "data": THUMB, "next": 0, "err": 0}


@app.get("/rr_gcode")
def rr_gcode(req: Request, gcode: str):
    if not authed(req):
        return JSONResponse({"err": 1}, 401)
    run(gcode)
    return {"buff": 255}


@app.post("/rr_upload")
async def rr_upload(req: Request, name: str):
    if not authed(req):
        return JSONResponse({"err": 1}, 401)
    FILES[name] = len(await req.body())
    return {"err": 0}


# ------------------------------------------------------------ modalità SBC (/machine)
@app.get("/machine/connect")
def machine_connect(password: str = ""):
    if CFG["password"] and password != CFG["password"]:
        return PlainTextResponse("Forbidden", 403)
    key = f"sbc-{len(SESSIONS)}"
    SESSIONS.add(key)
    return {"sessionKey": key}


@app.get("/machine/status")
def machine_status(req: Request):
    return model() if authed(req) else PlainTextResponse("Forbidden", 403)


@app.post("/machine/code")
async def machine_code(req: Request):
    if not authed(req):
        return PlainTextResponse("Forbidden", 403)
    run((await req.body()).decode())
    return PlainTextResponse("")


@app.put("/machine/file/{path:path}")
async def machine_file(req: Request, path: str):
    if not authed(req):
        return PlainTextResponse("Forbidden", 403)
    FILES[urllib.parse.unquote(path)] = len(await req.body())
    return PlainTextResponse("", 201)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--sbc", action="store_true")
    ap.add_argument("--password", default="reprap")
    ap.add_argument("--duration", type=int, default=40)
    ap.add_argument("--rrf37", action="store_true", help="risposte come RRF 3.7 (Elemento): mappature dei riscaldatori, T1 guasto")
    a = ap.parse_args()
    CFG.update(password=a.password, sbc=a.sbc, rrf37=a.rrf37)
    M["dur"] = a.duration
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
