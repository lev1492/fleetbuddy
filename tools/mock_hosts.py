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

import socket
import threading

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response

app = FastAPI()
K = {"kind": "octoprint", "dur": 30}
P = {"state": "idle", "file": None, "t0": 0, "paused_at": None, "speed": 100, "result": None, "jobid": 0,
     "tool": [22.0, 0], "bed": [21.0, 0], "chamber": [20.0, 0]}


def tick():
    for h in (P["tool"], P["bed"], P["chamber"]):
        h[0] += (max(h[1], 20) - h[0]) * 0.5
    if P["state"] == "printing" and time.time() - P["t0"] >= K["dur"]:
        P.update(state="idle", result="complete", ended=time.time())


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
        P.update(paused_at=progress(), state="idle", result="cancelled", ended=time.time())


def gcode(cmd: str):
    print("G-code:", cmd)
    for c in cmd.split("\n"):
        if m := re.match(r"(?:M104|M109).*S([\d.]+)", c) or re.match(r"SET_HEATER_TEMPERATURE HEATER=extruder TARGET=([\d.]+)", c):
            P["tool"][1] = float(m.group(1))
        elif m := re.match(r"M140 S([\d.]+)", c) or re.match(r"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=([\d.]+)", c):
            P["bed"][1] = float(m.group(1))
        elif m := re.match(r"SET_HEATER_TEMPERATURE HEATER=heater_bed(\d) TARGET=([\d.]+)", c):
            P[f"bed{m.group(1)}"] = float(m.group(2))
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


@app.get("/downloads/files/local/{path:path}")
def o_download(path: str, req: Request):
    data = open("/tmp/prusa.gcode", "rb").read()
    rng = req.headers.get("range", "")
    if m := re.match(r"bytes=(\d+)-(\d+)", rng):
        return Response(data[int(m.group(1)):int(m.group(2)) + 1], status_code=206)
    if m := re.match(r"bytes=-(\d+)", rng):
        return Response(data[-int(m.group(1)):], status_code=206)
    return Response(data)


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
    return {"job": {"file": {"name": P["file"], "path": P["file"], "origin": "local", "size": 1000},
                    "filament": {"tool0": {"length": 1520.3, "volume": 3.65}} if P["file"] else {}}, "state": P["state"],
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
                                   "gcode_move", "fan", "heater_generic chamber", "heater_generic heater_bed1",
                                   "heater_generic heater_bed2", "heater_generic heater_bed3"]}}


@app.get("/server/files/metadata")
def m_meta(filename: str):
    return {"result": {"filename": filename, "thumbnails": [{"width": 32, "height": 32, "relative_path": ".thumbs/x-32x32.png"}],
                       "slicer": "ElegooSlicer", "object_height": 30.0, "layer_height": 0.2, "first_layer_height": 0.3,
                       "filament_type": "PETG", "filament_colors": ["#FF8000"], "filament_total": 6700.0, "filament_weight_total": 20.0}}


@app.get("/server/files/gcodes/{path:path}")
def m_thumb(path: str):
    return Response(PNG, media_type="image/png")


@app.get("/server/webcams/list")
def m_cams():
    return {"result": {"webcams": [{"name": "cam", "snapshot_url": "/webcam/?action=snapshot"}]}}


@app.get("/printer/objects/query")
def m_query():
    tick()
    st = {"idle": "complete" if P["result"] == "complete" else "cancelled" if P["result"] == "cancelled" else "standby"}.get(P["state"], P["state"])
    return {"result": {"status": {
        "print_stats": {"state": st, "filename": P["file"] or "", "print_duration": max(1, progress() * K["dur"]),
                        "filament_used": round(progress() * 6700, 1) if P["file"] else 0,
                        "info": {} if K.get("no_layerinfo") else {"current_layer": int(progress() * 100), "total_layer": 100}},
        "virtual_sdcard": {"progress": progress()}, "display_status": {"progress": progress()},
        "extruder": {"temperature": round(P["tool"][0], 1), "target": P["tool"][1]},
        "heater_bed": {"temperature": round(P["bed"][0], 1), "target": P["bed"][1]},
        "heater_generic chamber": {"temperature": round(P["chamber"][0], 1), "target": P["chamber"][1]},
        **{f"heater_generic heater_bed{i}": {"temperature": round(P["bed"][0] - i, 1), "target": P.get(f"bed{i}", 0)} for i in (1, 2, 3)},
        "gcode_move": {"speed_factor": P["speed"] / 100, "gcode_position": [10, 10, round(0.3 + progress() * 29.7, 2), 0]},
        "fan": {"speed": 0.5 if P["state"] == "printing" else 0}}}}


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


# ------------------------------------------------------------------ Elegoo SDCP (Centauri Carbon)
MAINBOARD = "c0ffee00c0ffee00"


def sdcp_status() -> dict:
    tick()
    code = {"printing": 13, "paused": 6}.get(P["state"]) or {"complete": 9, "cancelled": 8}.get(P["result"], 0)
    return {"Status": {"CurrentStatus": [1] if P["state"] in ("printing", "paused") else [0],
                       "TempOfNozzle": round(P["tool"][0], 1), "TempTargetNozzle": P["tool"][1],
                       "TempOfHotbed": round(P["bed"][0], 1), "TempTargetHotbed": P["bed"][1],
                       "TempOfBox": round(P["chamber"][0], 1), "TempTargetBox": 0,
                       "CurrentFanSpeed": {"ModelFan": 100 if P["state"] == "printing" else 0, "AuxiliaryFan": 0, "BoxFan": 30},
                       "PrintInfo": {"Status": code, "CurrentLayer": int(progress() * 120), "TotalLayer": 120,
                                     "CurrentTicks": int(progress() * K["dur"]), "TotalTicks": K["dur"],
                                     "Filename": P["file"] or "", "TaskId": f"task{P['jobid']}", "PrintSpeedPct": 100,
                                     "Progress": int(progress() * 100)}},
            "MainboardID": MAINBOARD, "TimeStamp": int(time.time()), "Topic": f"sdcp/status/{MAINBOARD}"}


@app.websocket("/websocket")
async def sdcp_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            d = msg["Data"]
            cmd, ack, extra = d["Cmd"], 0, {}
            if cmd == 128:
                fname = d["Data"]["Filename"].rsplit("/", 1)[-1]
                if fname not in UPLOADS:
                    ack = 1
                else:
                    start(fname)
                    print("SDCP avvio:", d["Data"])
            elif cmd == 129:
                pause()
            elif cmd == 131:
                resume()
            elif cmd == 130:
                cancel()
            elif cmd == 386:
                extra = {"VideoUrl": f"127.0.0.1:{K['port']}/video"}
            await ws.send_text(json.dumps({"Id": "x", "Topic": f"sdcp/response/{MAINBOARD}",
                                           "Data": {"Cmd": cmd, "RequestID": d["RequestID"], "MainboardID": MAINBOARD,
                                                    "Data": {"Ack": ack, **extra}}}))
            await ws.send_text(json.dumps(sdcp_status()))
    except WebSocketDisconnect:
        pass


UPLOADS: dict[str, int] = {}


@app.post("/uploadFile/upload")
async def sdcp_upload(req: Request):
    form = await req.form()
    data = await form["File"].read()
    UPLOADS[form["File"].filename] = UPLOADS.get(form["File"].filename, 0) + len(data)
    return {"code": "000000", "success": True, "messages": None}


@app.get("/video")
def sdcp_video():
    frame = b"\xff\xd8\xff\xe0" + b"mjpegframe" * 20 + b"\xff\xd9"
    body = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
    return Response(body * 3, media_type="multipart/x-mixed-replace; boundary=frame")


def udp_discovery(port: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    while True:
        data, addr = s.recvfrom(1024)
        if data.strip() == b"M99999":
            s.sendto(json.dumps({"Id": "x", "Data": {"Name": "Centauri", "MachineName": "Centauri Carbon", "BrandName": "ELEGOO",
                                                     "MainboardIP": "127.0.0.1", "MainboardID": MAINBOARD,
                                                     "ProtocolVersion": "V3.0.0", "FirmwareVersion": "V1.1.29"}}).encode(), addr)


# ------------------------------------------------------------------ Raise3D (Raise Touch Remote Access API)
R3D = {"token": "", "files": {"Local": ["pezzo.gcode", "staffa.gcode"] + [f"lotto_{i:02d}.gcode" for i in range(1, 29)],
                              "Local/clienti": ["ordine42.gcode"]}}


def r3d_ok(data=None):
    return {"status": 1, **({"data": data} if data is not None else {})}


def r3d_auth(token: str):
    return token and token == R3D["token"]


def r3d_login(sign: str, timestamp: str):
    import hashlib as h
    exp = h.md5(h.sha1(f"password=segreta&timestamp={timestamp}".encode()).hexdigest().encode()).hexdigest()
    if sign != exp:
        return {"error": {"code": 10000, "msg": "sign error or password error"}, "status": 0}
    R3D["token"] = secrets.token_hex(16)
    return r3d_ok({"token": R3D["token"]})


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def r3d_api(path: str, req: Request, token: str = ""):
    if path == "login":                       # la rota generica /v1/... è registrata prima di /v1/login
        return it_login_or_r3d(req)
    if K["kind"] == "intamsys":
        return intamsys_api(path, req, {})
    if not r3d_auth(token):
        return {"error": {"code": 401, "msg": "token error"}, "status": 0}
    tick()
    body = await req.json() if req.method == "POST" else {}
    rs = {"printing": "running", "paused": "paused"}.get(P["state"]) or ("completed" if P["result"] == "complete" else "idle")
    if path == "printer/system":
        return r3d_ok({"Serial_number": "12345678942", "api_version": "0.1.0.307", "firmware_version": "1.1.6-rev1",
                       "machine_name": "raise3d", "model": "Raise3D Pro3 Plus", "nozzies_num": 2, "version": "0.9.3"})
    if path == "printer/camera":
        return r3d_ok({"camerserver_URI": f":{K['port']}/api/v1/camera/auth_stream", "is_camera_connected": True,
                       "user_name": "raise3d_camera", "password": "368d8d"})
    if path == "printer/runningstatus":
        return r3d_ok({"running_status": rs})
    if path == "printer/basic":
        return r3d_ok({"fan_cur_speed": 50 if P["state"] == "printing" else 0, "fan_tar_speed": 0, "feed_cur_rate": P["speed"],
                       "feed_tar_rate": P["speed"], "heatbed_cur_temp": round(P["bed"][0], 1), "heatbed_tar_temp": P["bed"][1]})
    if path in ("printer/nozzle1", "printer/nozzle2"):
        t = P["tool"] if path.endswith("1") else P["chamber"]
        return r3d_ok({"flow_cur_rate": 100, "flow_tar_rate": 100, "nozzle_cur_temp": round(t[0], 1), "nozzle_tar_temp": t[1]})
    if path in ("printer/nozzle1/temp/set", "printer/nozzle2/temp/set"):
        (P["tool"] if "nozzle1" in path else P["chamber"])[1] = body["temperature"]
        return r3d_ok()
    if path == "printer/heatbedtemp/set":
        P["bed"][1] = body["temperature"]
        return r3d_ok()
    if path == "printer/feedrate/set":
        P["speed"] = body["feedrate"]
        return r3d_ok()
    if path == "job/currentjob":
        if not P["file"]:
            return {"error": {"code": 10004, "msg": "no job"}, "status": 0}
        js = {"printing": "running", "paused": "paused"}.get(P["state"]) or ("completed" if P["result"] == "complete" else "stopped")
        return r3d_ok({"file_name": P["file"], "print_progress": round(progress() * 100, 2), "printed_layer": int(progress() * 200),
                       "printed_time": int(progress() * K["dur"]), "job_id": f"JOB{P['jobid']}", "total_layer": 200,
                       "total_time": K["dur"], "job_status": js})
    if path == "job/currentjob/set":
        {"pause": pause, "resume": resume, "stop": cancel}[body["operate"]]()
        return r3d_ok()
    if path == "job/newjob/set":
        folder, name = body["file_path"].rsplit("/", 1)
        if name not in R3D["files"].get(folder, []):
            return {"error": {"code": 10005, "msg": "root path error"}, "status": 0}
        start(body["file_path"])
        return r3d_ok()
    if path == "fileops/list":
        mx = int(req.query_params.get("max_num", 0))
        if not 1 <= mx <= 24:                       # come la stampante reale
            return {"error": {"code": 10002, "msg": "max_num error, range(1~24) the value of max_num is out of the range (1~24)"}, "status": 0}
        pos = int(req.query_params.get("start_pos", 0))
        d = req.query_params.get("dir", "Local")
        subs = sorted({k[len(d) + 1:].split("/")[0] for k in R3D["files"] if k.startswith(d + "/")})
        items = [("d", x) for x in subs] + [("f", f) for f in R3D["files"].get(d, [])]
        page = items[pos:pos + mx]
        return r3d_ok({"dir_list": [x for k, x in page if k == "d"], "file_list": [{"data_path": "", "file_name": x} for k, x in page if k == "f"]})
    if False:
        subs = sorted({k[len(d) + 1:].split("/")[0] for k in R3D["files"] if k.startswith(d + "/")})
        return r3d_ok({"dir_list": subs, "file_list": [{"data_path": "", "file_name": f} for f in R3D["files"].get(d, [])]})
    return JSONResponse({"status": 0}, 404)


@app.get("/api/v1/camera/auth_stream")
def r3d_cam(req: Request):
    import base64
    if req.headers.get("authorization") != "Basic " + base64.b64encode(b"raise3d_camera:368d8d").decode():
        return Response(status_code=401)
    return sdcp_video()


# ------------------------------------------------------------------ UltiMaker (API /api/v1 + Connect facoltativo)
UM = {"auth": {}, "approve_after": 0, "connect": True}
UM_NONCE = secrets.token_hex(8)


def um_digest_ok(req: Request) -> bool:
    h = req.headers.get("authorization", "")
    if not h.startswith("Digest "):
        return False
    f = dict(re.findall(r'(\w+)="?([^",]+)"?', h))
    key = UM["auth"].get(f.get("username"), {}).get("key")
    if not key or UM["auth"][f["username"]]["status"] != "authorized":
        return False
    H = lambda x: hashlib.sha256(x.encode()).hexdigest()
    ha1, ha2 = H(f"{f['username']}:Jedi-API:{key}"), H(f"{req.method}:{f.get('uri')}")
    return f.get("response") == H(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:{f['qop']}:{ha2}")


def um_challenge():
    return Response(status_code=401, headers={"WWW-Authenticate": f'Digest realm="Jedi-API", nonce="{UM_NONCE}", qop="auth", algorithm="SHA-256"'})


@app.post("/api/v1/auth/request")
async def um_auth_request(req: Request):
    if UM.get("cloud"):
        return JSONResponse({"message": "Local authentication is disabled, use Digital Factory"}, 400)
    if UM.get("newfw"):            # firmware recente simulato: vuole un corpo JSON
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"message": "Expected a JSON body with 'application' and 'user'"}, 400)
        form = body
    else:
        form = await req.form()
    aid, key = secrets.token_hex(16), secrets.token_hex(32)
    UM["auth"][aid] = {"key": key, "status": "unknown", "t": time.time(), "user": form.get("user")}
    return {"id": aid, "key": key}


@app.get("/api/v1/auth/check/{aid}")
def um_auth_check(aid: str):
    a = UM["auth"].get(aid)
    if not a:
        return {"message": "unknown"}
    if a["status"] == "unknown" and time.time() - a["t"] > UM["approve_after"]:   # l'utente approva sul display
        a["status"] = "authorized"
    return {"message": a["status"]}


@app.get("/cluster-api/v1/system/authentication_mode/")
def um_auth_mode():
    return {"mode": "cloud" if UM.get("cloud") else "open"}


@app.get("/api/v1/system/{what}")
def um_system(what: str, req: Request):
    if UM.get("newfw") and not um_digest_ok(req):     # sui modelli recenti simulati serve l'autorizzazione
        return um_challenge()
    return {"name": "UM-S5-Officina", "firmware": "8.3.0", "variant": "Ultimaker S5"}.get(what, "")


@app.get("/api/v1/printer")
def um_printer():
    tick()
    return {"status": "printing" if P["state"] in ("printing", "paused") else "idle",
            "bed": {"temperature": {"current": round(P["bed"][0], 1), "target": P["bed"][1]}, "type": "glass"},
            "heads": [{"extruders": [{"hotend": {"id": "AA 0.4", "temperature": {"current": round(P["tool"][0], 1), "target": P["tool"][1]}}},
                                     {"hotend": {"id": "BB 0.4", "temperature": {"current": round(P["chamber"][0], 1), "target": P["chamber"][1]}}}]}]}


@app.get("/api/v1/print_job")
def um_job():
    tick()
    if not P["file"]:
        return JSONResponse({"message": "No printer job running"}, 404)
    st = {"printing": "printing", "paused": "paused"}.get(P["state"]) or "wait_cleanup"
    return {"name": P["file"], "state": st, "progress": round(progress(), 3), "time_elapsed": int(progress() * K["dur"]),
            "time_total": K["dur"], "uuid": f"job-{P['jobid']}", "result": {"complete": "Finished", "cancelled": "Aborted"}.get(P["result"], "")}


@app.put("/api/v1/print_job/state")
async def um_state(req: Request):
    if not um_digest_ok(req):
        return um_challenge()
    tgt = (await req.json())["target"]
    {"pause": pause, "print": resume, "abort": cancel}[tgt]()
    return Response(status_code=204)


@app.post("/api/v1/print_job")
async def um_upload(req: Request):
    if not um_digest_ok(req):
        return um_challenge()
    form = await req.form()
    start(form["jobname"])
    return JSONResponse({"message": "Print job accepted", "uuid": f"job-{P['jobid']}"}, 201)


@app.put("/api/v1/printer/bed/temperature")
async def um_bed(req: Request):
    if not um_digest_ok(req):
        return um_challenge()
    P["bed"][1] = float((await req.form())["temperature"])
    return Response(status_code=204)


@app.put("/api/v1/printer/heads/0/extruders/{n}/hotend/temperature")
async def um_hotend(n: int, req: Request):
    if not um_digest_ok(req):
        return um_challenge()
    (P["tool"] if n == 0 else P["chamber"])[1] = float((await req.form())["temperature"])
    return Response(status_code=204)


UM_MATERIALS = {
    "9cfe5bf1-bdc5-4beb-871a-52c70777842d": ("UltiMaker", "PLA", "Red", "#c8102e", 1.24),
    "86a89ceb-4159-47f6-ab97-e9953803d70f": ("UltiMaker", "PVA", "Natural", "#f2f2f2", 1.23),
}


@app.get("/api/v1/materials/{guid}")
def um_material(guid: str):
    if guid not in UM_MATERIALS:
        return JSONResponse({"message": "not found"}, 404)
    b, m, c, cc, d = UM_MATERIALS[guid]
    return (f'<?xml version="1.0" encoding="UTF-8"?><fdmmaterial xmlns="http://www.ultimaker.com/material" version="1.3">'
            f'<metadata><name><brand>{b}</brand><material>{m}</material><color>{c}</color></name><GUID>{guid}</GUID>'
            f'<color_code>{cc}</color_code></metadata><properties><density>{d}</density><diameter>2.85</diameter></properties></fdmmaterial>')


@app.get("/api/v1/history/print_jobs/{uid}")
def um_history(uid: str):
    # lo storico compare qualche secondo dopo la fine della stampa (per provare il recupero ritardato)
    if uid != f"job-{P['jobid']}" or not P["result"] or time.time() - P.get("ended", 0) < UM.get("history_delay", 0):
        return JSONResponse({"message": "Not found"}, 404)
    frac = 1.0 if P["result"] == "complete" else (P.get("paused_at") or 0.3)
    return {"uuid": uid, "name": P["file"], "result": "Finished" if P["result"] == "complete" else "Aborted",
            "material_0_guid": "9cfe5bf1-bdc5-4beb-871a-52c70777842d", "material_0_amount": round(5000 * frac),
            "material_1_guid": "86a89ceb-4159-47f6-ab97-e9953803d70f", "material_1_amount": round(1200 * frac),
            "time_elapsed": int(K["dur"] * frac), "time_total": K["dur"]}


@app.get("/api/v1/camera/0/snapshot")
def um_cam():
    return Response(b"\xff\xd8\xff\xe0umcam\xff\xd9", media_type="image/jpeg")


@app.get("/cluster-api/v1/printers/")
def um_cluster_printers(req: Request):
    if UM.get("newfw") and not um_digest_ok(req):
        return um_challenge()
    if not UM["connect"]:
        return JSONResponse({"message": "not found"}, 404)
    return [{"uuid": "p-uuid-1", "ip_address": "127.0.0.1", "friendly_name": "UM-S5-Officina", "machine_variant": "Ultimaker S5",
             "status": "printing" if P["file"] else "idle", "firmware_version": "8.3.0", "maintenance_required": True,
             "firmware_update_status": "update_available", "latest_available_firmware": "8.4.0",
             "air_manager": {"supported": True, "status": "available", "filter_status": "replacement_near", "filter_age": 870, "filter_max_age": 1000},
             "errors": [{"code": "air_manager_filter_needs_replacement", "uuid": "e1"}],
             "configuration": [{"extruder_index": 0, "print_core_id": "AA 0.4", "material": {"brand": "UltiMaker", "material": "PLA", "color": "Red"}},
                               {"extruder_index": 1, "print_core_id": "BB 0.4", "material": {"brand": "UltiMaker", "material": "PVA", "color": "Natural"}}],
             "material_station": {"supported": True, "status": "available", "material_slots": [
                 {"slot_index": i, "extruder_index": e, "print_core_id": "AA 0.4" if e == 0 else "BB 0.4", "compatible": True,
                  "material_remaining": r, "material_empty": r == 0, "material": {"brand": "UltiMaker", "material": m, "color": c} if m else {}}
                 for i, (m, c, r, e) in enumerate([("PLA", "Red", 0.8, 0), ("PVA", "Natural", 0.45, 1), ("Tough PLA", "Black", 1.0, 0),
                                                   ("", "", -1, -1), ("PETG", "Blue", 0.0, 0), ("PVA", "Natural", 0.9, 1)])]},
             "faults": []}]


@app.get("/cluster-api/v1/print_jobs/printing")
def um_cluster_jobs():
    if not UM["connect"]:
        return JSONResponse({}, 404)
    return [{"uuid": f"job-{P['jobid']}", "name": P["file"], "printer_uuid": "p-uuid-1", "status": "printing"}] if P["file"] and P["state"] in ("printing", "paused") else []


@app.get("/cluster-api/v1/print_jobs/{uid}/preview_image")
def um_preview(uid: str):
    return Response(PNG, media_type="image/png")


@app.get("/fault-api/v1/faults/")
def um_faults():
    if not UM["connect"]:
        return JSONResponse({}, 404)
    return [{"code": "air_manager_filter_needs_replacement", "severity": "WARNING", "message": "Filter needs replacement",
             "uuid": "f1", "timestamp": "2026-11-06T08:00:00", "data": {}}]


@app.get("/material-station-api/v1/material-station/")
def um_misp():
    if not UM["connect"]:
        return JSONResponse({}, 404)
    return {"status": "available", "current_humidity": 23.4, "dehumidifier_status": "operational", "door_opened": False, "slots": []}


# ------------------------------------------------------------------ INTAMSYS (INTAMTOUCH, porta 5588)
IT = {"tokens": set(), "files": ["FPRO610HT_Tensile-1B.gcode", "staffa_PEEK.gcode"]}


def it_err(code, msg):
    return {"error": {"err_code": code, "message": msg}, "status": 0}


@app.get("/v1/login")
def it_login_or_r3d(req: Request):
    if K["kind"] == "raise3d":
        return r3d_login(req.query_params.get("sign", ""), req.query_params.get("timestamp", ""))
    # come il firmware reale in uso: password in chiaro (con --md5 si simula la formula del documento)
    expected = hashlib.md5(b"password=segreta").hexdigest() if K.get("md5") else "segreta"
    if req.query_params.get("signin") != expected:
        return it_err(50001, "error password")
    tok = secrets.token_hex(16)
    IT["tokens"].add(tok)
    return {"data": {"token": tok}, "status": 1}


def intamsys_api(path: str, req: Request, body: dict):
    if req.query_params.get("token") not in IT["tokens"]:
        return it_err(50000, "token is invalid")
    tick()
    temp = lambda h: {"current_temperature": round(h[0], 1), "target_temperature": str(h[1])}
    if path == "printer/system":
        return {"data": {"model": "FUNMAT PRO 610HT", "firmware_version": "PRO610_V1.0.5.16", "software_version": "V1.2.4.19", "api_version": "v1"}, "status": 1}
    if path == "printer/runningstatus":
        return {"data": {"running_status": {"printing": "printing", "paused": "paused"}.get(P["state"], "idle")}, "status": 1}
    if path == "printer/basictemperature":
        return {"data": {"buildplate": temp(P["bed"]), "chamber": temp(P["chamber"]), "nozzle1": temp(P["tool"]), "nozzle2": temp([22, 0]),
                         **{f"filament{i}": {"current_temperature": 49, "target_temperature": 50} for i in range(1, 5)},
                         "water_cooling": {"current_temperature": 18}}, "status": True}
    if path == "printer/filamentweight":
        used = progress() * 35 if P["file"] else 0          # il filamento consumato resta consumato
        base = [820 - used * 0.7, 410 - used * 0.3, 0, 1150]
        return {"data": {f"filament{i}": {"current_weight": round(w, 1)} for i, w in enumerate(base, 1)}, "status": True}
    if path == "printer/currentalarm":
        return {"data": {"code": 0, "description": "Printer runs well", "action": "No action"}, "status": 1}
    if path == "job/currentjob":
        js = {"printing": "printing", "paused": "paused"}.get(P["state"]) or {"complete": "completed", "cancelled": "aborted"}.get(P["result"], "aborted")
        return {"data": {"file_name": P["file"] or "", "job_status": js, "print_progress": round(progress() * 100, 1),
                         "estimated_total_time": K["dur"], "real_printed_time": int(progress() * K["dur"])}, "status": 1}
    if path == "fileops/printfiles":
        return {"data": {"file_list": IT["files"]}, "status": 1}
    if path == "job/newjob/start":
        name = req.query_params.get("name")
        if name not in IT["files"]:
            return it_err(50002, "file name or file path is wrong")
        if P["state"] in ("printing", "paused"):
            return it_err(50003, "printer is running a job")
        start(name)
        return {"status": 1}
    if path in ("job/currentjob/pause", "job/currentjob/resume", "job/currentjob/abort"):
        {"pause": pause, "resume": resume, "abort": cancel}[path.rsplit("/", 1)[1]]()
        return {"status": 1}
    return JSONResponse({"status": 0}, 404)


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0f00f0004850180848a8c210000000049454e44ae426082")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["octoprint", "moonraker", "prusalink", "repetier", "elegoo", "raise3d", "ultimaker", "intamsys"])
    ap.add_argument("--no-connect", action="store_true", help="UltiMaker senza UltiMaker Connect (solo /api/v1)")
    ap.add_argument("--no-layerinfo", action="store_true", help="Moonraker senza SET_PRINT_STATS_INFO (come le Elegoo)")
    ap.add_argument("--md5", action="store_true", help="INTAMSYS: accesso con la formula del documento invece della password in chiaro")
    ap.add_argument("--newfw", action="store_true", help="UltiMaker recente (S6/S8/Factor 4): richiesta in JSON, letture protette")
    ap.add_argument("--cloud", action="store_true", help="UltiMaker in modalità di accesso cloud (Digital Factory)")
    ap.add_argument("--approve-after", type=int, default=4, help="UltiMaker: secondi prima dell'approvazione sul display")
    ap.add_argument("--history-delay", type=int, default=0, help="UltiMaker: secondi prima che la stampa compaia nello storico")
    ap.add_argument("--udp-port", type=int, default=3000)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--duration", type=int, default=30)
    a = ap.parse_args()
    K.update(kind=a.kind, dur=a.duration, port=a.port, md5=a.md5, no_layerinfo=a.no_layerinfo)
    UM.update(connect=not a.no_connect, approve_after=a.approve_after, newfw=a.newfw, cloud=a.cloud, history_delay=a.history_delay)
    if a.kind == "elegoo":
        threading.Thread(target=udp_discovery, args=(a.udp_port,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
