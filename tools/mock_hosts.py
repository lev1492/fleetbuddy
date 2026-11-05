#!/usr/bin/env python3
"""Stampanti simulate per provare i connettori diretti senza hardware.

  python -m tools.mock_hosts --kind octoprint --port 5000     (chiave API "chiave")
  python -m tools.mock_hosts --kind moonraker --port 7125
  python -m tools.mock_hosts --kind prusalink --port 8081     (utente maker, password "segreta", Digest)
  python -m tools.mock_hosts --kind repetier  --port 3344     (chiave API "chiave", stampante "ender")
"""
import argparse
import hashlib
import json
import re
import secrets
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

app = FastAPI()
K = {"kind": "octoprint", "dur": 30}
P = {"state": "idle", "file": None, "t0": 0, "paused_at": None, "speed": 100, "result": None, "jobid": 0,
     "tool": [22.0, 0], "bed": [21.0, 0], "chamber": [20.0, 0]}


def tick():
    for h in (P["tool"], P["bed"], P["chamber"]):
        h[0] += (max(h[1], 20) - h[0]) * 0.5
    if P["state"] == "printing" and time.time() - P["t0"] >= K["dur"]:
        P.update(state="idle", result="complete")


def progress() -> float:
    if not P["file"]:
        return 0.0
    if P["state"] == "paused":
        return P["paused_at"]
    if P["state"] == "printing":
        return min(1.0, (time.time() - P["t0"]) / K["dur"])
    return 1.0 if P["result"] == "complete" else (P["paused_at"] or 0.3)


def start(name: str):
    P.update(state="printing", file=name, t0=time.time(), paused_at=None, result=None, jobid=P["jobid"] + 1)


def pause():
    if P["state"] == "printing":
        P.update(state="paused", paused_at=progress())


def resume():
    if P["state"] == "paused":
        P.update(state="printing", t0=time.time() - P["paused_at"] * K["dur"])


def cancel():
    if P["state"] in ("printing", "paused"):
        P.update(paused_at=progress(), state="idle", result="cancelled")


def gcode(cmd: str):
    print("G-code:", cmd)
    for c in cmd.split("\n"):
        if m := re.match(r"(?:M104|M109).*S([\d.]+)", c) or re.match(r"SET_HEATER_TEMPERATURE HEATER=extruder TARGET=([\d.]+)", c):
            P["tool"][1] = float(m.group(1))
        elif m := re.match(r"M140 S([\d.]+)", c) or re.match(r"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=([\d.]+)", c):
            P["bed"][1] = float(m.group(1))
        elif m := re.match(r"M141 S([\d.]+)", c) or re.match(r"SET_HEATER_TEMPERATURE HEATER=chamber TARGET=([\d.]+)", c):
            P["chamber"][1] = float(m.group(1))
        elif m := re.match(r"M220 S(\d+)", c):
            P["speed"] = int(m.group(1))
        elif c.strip() == "@pause":
            pause()


def key_ok(req: Request) -> bool:
    return req.headers.get("x-api-key") == "chiave"


# ------------------------------------------------------------------ OctoPrint
@app.get("/api/version")
def o_version(req: Request):
    if K["kind"] == "prusalink":
        return p_version(req)
    if not key_ok(req):
        return JSONResponse({"error": "Invalid API key"}, 403)
    return {"api": "0.1", "server": "1.10.2", "text": "OctoPrint 1.10.2"}


@app.get("/api/settings")
def o_settings(req: Request):
    return {"webcam": {"webcamEnabled": True, "snapshotUrl": "/webcam/?action=snapshot"}} if key_ok(req) else JSONResponse({}, 403)


@app.get("/webcam/")
def o_cam():
    return Response(b"\xff\xd8\xff\xe0fakejpeg", media_type="image/jpeg")


@app.get("/api/printer")
def o_printer(req: Request):
    if not key_ok(req):
        return JSONResponse({}, 403)
    tick()
    st = P["state"]
    return {"temperature": {"tool0": {"actual": round(P["tool"][0], 1), "target": P["tool"][1]},
                            "bed": {"actual": round(P["bed"][0], 1), "target": P["bed"][1]}},
            "state": {"text": st, "flags": {"operational": True, "printing": st == "printing", "paused": st == "paused",
                                            "pausing": False, "cancelling": False, "error": False, "ready": st == "idle",
                                            "closedOrError": False}}}


@app.get("/api/job")
def o_job(req: Request):
    if not key_ok(req):
        return JSONResponse({}, 403)
    tick()
    pr = progress()
    return {"job": {"file": {"name": P["file"], "size": 1000}}, "state": P["state"],
            "progress": {"completion": round(pr * 100, 2) if P["file"] else None, "printTime": 10,
                         "printTimeLeft": int(K["dur"] * (1 - pr)) if P["state"] != "idle" else 0}}


@app.post("/api/job")
async def o_job_cmd(req: Request):
    b = await req.json()
    if b["command"] == "pause":
        pause() if b.get("action") == "pause" else resume()
    elif b["command"] == "cancel":
        cancel()
    return Response(status_code=204)


@app.post("/api/files/local")
async def o_upload(req: Request):
    form = await req.form()
    if form.get("print") == "true":
        start(form["file"].filename)
    return JSONResponse({"done": True}, 201)


@app.post("/api/printer/{what}")
async def o_temp(what: str, req: Request):
    b = await req.json()
    if what == "tool":
        P["tool"][1] = list(b["targets"].values())[0]
    elif what == "bed":
        P["bed"][1] = b["target"]
    elif what == "printhead":
        P["speed"] = b["factor"]
    return Response(status_code=204)


# ------------------------------------------------------------------ Moonraker
@app.get("/printer/info")
def m_info():
    if K["kind"] == "repetier":
        return {"name": "Repetier-Server", "version": "1.4.12", "printers": [{"slug": "ender"}]}
    return {"result": {"state": "ready", "state_message": "Printer is ready", "hostname": "voron", "software_version": "v0.12.0-300"}}


@app.get("/printer/objects/list")
def m_objects():
    return {"result": {"objects": ["print_stats", "virtual_sdcard", "display_status", "heater_bed", "extruder",
                                   "gcode_move", "fan", "heater_generic chamber"]}}


@app.get("/server/webcams/list")
def m_cams():
    return {"result": {"webcams": [{"name": "cam", "snapshot_url": "/webcam/?action=snapshot"}]}}


@app.get("/printer/objects/query")
def m_query():
    tick()
    st = {"idle": "complete" if P["result"] == "complete" else "cancelled" if P["result"] == "cancelled" else "standby"}.get(P["state"], P["state"])
    return {"result": {"status": {
        "print_stats": {"state": st, "filename": P["file"] or "", "print_duration": max(1, progress() * K["dur"]),
                        "info": {"current_layer": int(progress() * 100), "total_layer": 100}},
        "virtual_sdcard": {"progress": progress()}, "display_status": {"progress": progress()},
        "extruder": {"temperature": round(P["tool"][0], 1), "target": P["tool"][1]},
        "heater_bed": {"temperature": round(P["bed"][0], 1), "target": P["bed"][1]},
        "heater_generic chamber": {"temperature": round(P["chamber"][0], 1), "target": P["chamber"][1]},
        "gcode_move": {"speed_factor": P["speed"] / 100}, "fan": {"speed": 0.5 if P["state"] == "printing" else 0}}}}


@app.post("/printer/print/{what}")
def m_print(what: str):
    {"pause": pause, "resume": resume, "cancel": cancel}[what]()
    return {"result": "ok"}


@app.post("/printer/gcode/script")
def m_gcode(script: str):
    gcode(script)
    return {"result": "ok"}


@app.post("/server/files/upload")
async def m_upload(req: Request):
    form = await req.form()
    if form.get("print") == "true":
        start(form["file"].filename)
    return JSONResponse({"result": {"print_started": True}}, 201)


# ------------------------------------------------------------------ PrusaLink (Digest maker / segreta)
NONCE = secrets.token_hex(8)


def digest_ok(req: Request) -> bool:
    h = req.headers.get("authorization", "")
    if not h.startswith("Digest "):
        return False
    f = dict(re.findall(r'(\w+)="?([^",]+)"?', h))
    ha1 = hashlib.md5(f"maker:Printer API:segreta".encode()).hexdigest()
    ha2 = hashlib.md5(f"{req.method}:{f.get('uri')}".encode()).hexdigest()
    if f.get("qop"):
        exp = hashlib.md5(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:{f['qop']}:{ha2}".encode()).hexdigest()
    else:
        exp = hashlib.md5(f"{ha1}:{f['nonce']}:{ha2}".encode()).hexdigest()
    return f.get("response") == exp


def challenge():
    return Response(status_code=401, headers={"WWW-Authenticate": f'Digest realm="Printer API", nonce="{NONCE}", qop="auth"'})


def p_version(req: Request):
    if not digest_ok(req):
        return challenge()
    return {"api": "2.0.0", "server": "2.1.2", "text": "PrusaLink", "hostname": "MK4S", "firmware": "6.1.3"}


@app.get("/api/v1/status")
def p_status(req: Request):
    if not digest_ok(req):
        return challenge()
    tick()
    state = {"printing": "PRINTING", "paused": "PAUSED"}.get(P["state"]) or \
        {"complete": "FINISHED", "cancelled": "STOPPED"}.get(P["result"], "IDLE")
    st = {"printer": {"state": state, "temp_nozzle": round(P["tool"][0], 1), "target_nozzle": P["tool"][1],
                      "temp_bed": round(P["bed"][0], 1), "target_bed": P["bed"][1], "speed": P["speed"]}}
    if P["file"]:
        st["job"] = {"id": P["jobid"], "progress": round(progress() * 100), "time_remaining": int(K["dur"] * (1 - progress()))}
    return st


@app.get("/api/v1/job")
def p_job(req: Request):
    if not digest_ok(req):
        return challenge()
    return {"id": P["jobid"], "file": {"name": P["file"], "display_name": P["file"]}} if P["file"] else Response(status_code=204)


@app.put("/api/v1/job/{jid}/{what}")
def p_jobcmd(jid: int, what: str, req: Request):
    if not digest_ok(req):
        return challenge()
    (pause if what == "pause" else resume)()
    return Response(status_code=204)


@app.delete("/api/v1/job/{jid}")
def p_stop(jid: int, req: Request):
    if not digest_ok(req):
        return challenge()
    cancel()
    return Response(status_code=204)


@app.get("/api/v1/storage")
def p_storage(req: Request):
    return {"storage_list": [{"path": "/usb/", "name": "usb", "available": True, "read_only": False}]} if digest_ok(req) else challenge()


@app.put("/api/v1/files/{storage}/{name}")
async def p_upload(storage: str, name: str, req: Request):
    if not digest_ok(req):
        return challenge()
    await req.body()
    if req.headers.get("print-after-upload") == "?1":
        start(name)
    return Response(status_code=201)


# ------------------------------------------------------------------ Repetier-Server
@app.get("/printer/list")
def r_list(req: Request):
    tick()
    if not key_ok(req):
        return JSONResponse({"error": "wrong key"}, 403)
    active = P["state"] in ("printing", "paused")
    return {"data": [{"slug": "ender", "name": "Ender 3", "online": 1, "job": P["file"] if active else "none",
                      "jobid": P["jobid"], "done": round(progress() * 100, 1) if active else 0, "paused": P["state"] == "paused",
                      "printTime": K["dur"], "printedTimeComp": progress() * K["dur"]}]}


@app.get("/printer/api/{slug}")
def r_api(slug: str, req: Request, a: str, data: str = "{}"):
    if not key_ok(req):
        return JSONResponse({}, 403)
    tick()
    if a == "stateList":
        return {slug: {"extruder": [{"tempRead": round(P["tool"][0], 1), "tempSet": P["tool"][1]}],
                       "heatedBeds": [{"tempRead": round(P["bed"][0], 1), "tempSet": P["bed"][1]}],
                       "fans": [{"on": True, "voltage": 128}], "speedMultiply": P["speed"], "layer": int(progress() * 100)}}
    if a == "send":
        gcode(json.loads(data)["cmd"])
    elif a == "continueJob":
        resume()
    elif a == "stopJob":
        cancel()
    return {}


@app.post("/printer/job/{slug}")
async def r_upload(slug: str, req: Request):
    form = await req.form()
    start(form["filename"].filename)
    return {}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["octoprint", "moonraker", "prusalink", "repetier"])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--duration", type=int, default=30)
    a = ap.parse_args()
    K.update(kind=a.kind, dur=a.duration)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
