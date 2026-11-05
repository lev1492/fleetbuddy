"""Normalizza la risposta di GET /v1/hub/devices in un modello semplice per la UI
e calcola la mappatura automatica filamento -> slot (sezione 5.3 del PDF)."""
from __future__ import annotations

import re

from .i18n import _

ACTIVE_STATES = {"PREPARE", "RUNNING", "PAUSE", "PAUSED", "SLICING"}
DUAL_NOZZLE = {"H2D", "H2C", "H2D PRO", "X2D"}

# Firmware minimo per il Fleet Hub (Technical White Paper v1.0, par. 1.6)
MIN_FIRMWARE = {
    "A1": "01.08.00.00", "A1MINI": "01.08.00.00", "P1P": "01.09.00.00", "P1S": "01.09.00.00",
    "X1C": "01.09.00.00", "X1E": "01.01.03.12", "H2D": "01.02.00.00", "H2DPRO": "01.01.00.00",
    "H2S": "01.02.00.00", "P2S": "01.02.00.00", "X2D": "01.01.00.00",
}


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return ()


def firmware_below_min(model: str, version: str) -> str:
    """Restituisce la versione minima richiesta se quella installata è inferiore, altrimenti ''."""
    need = MIN_FIRMWARE.get((model or "").upper().replace(" ", "").replace("-", ""))
    have = _ver(version)
    if need and have and have < _ver(need):
        return need
    return ""

# Filamenti generici (tabella 4.5.1 del PDF) per la modifica slot
FILAMENT_PRESETS = [
    {"type": "PLA", "idx": "GFA00", "min": 190, "max": 240},
    {"type": "PETG", "idx": "GFG00", "min": 220, "max": 270},
    {"type": "PETG-CF", "idx": "GFG50", "min": 240, "max": 270},
    {"type": "TPU", "idx": "GFU00", "min": 200, "max": 250},
    {"type": "ABS", "idx": "GFB00", "min": 240, "max": 270},
    {"type": "ABS-GF", "idx": "GFB50", "min": 240, "max": 270},
    {"type": "ASA", "idx": "GFB01", "min": 240, "max": 270},
    {"type": "ASA-AERO", "idx": "GFB02", "min": 240, "max": 280},
]


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def hms_code(entry: dict) -> str:
    """attr=50336256, code=131073 -> '0300120000020001'"""
    return f"{int(entry.get('attr', 0)):08X}{int(entry.get('code', 0)):08X}"


def print_error_code(v) -> str:
    n = int(_num(v))
    return f"{n:08X}" if n else ""


def tray_label(ams_id: int, slot_id: int, model: str) -> str:
    # le bobine esterne hanno un codice che il browser traduce
    if ams_id == 255:
        return "EXT_R" if model in DUAL_NOZZLE else "EXT"
    if ams_id == 254:
        return "EXT_L"
    if 128 <= ams_id <= 135:
        return f"HT-{chr(65 + ams_id - 128)}"
    return f"{chr(65 + ams_id)}{slot_id + 1}"


def _color(c: str | None) -> str:
    c = (c or "").strip().lstrip("#")
    if len(c) >= 6:
        return "#" + c[:6].upper()
    return ""


def _remain(t: dict) -> tuple[int | None, int | None]:
    """Capacità residua della bobina in % e peso nominale in g, se la stampante li riporta.

    Non sono documentati nelle API del Hub v1.0.0: le stampanti Bambu li inviano per le bobine con RFID
    quando la stima del filamento residuo è attiva; -1 o campo assente = sconosciuto."""
    try:
        r = int(float(t.get("remain")))
    except (TypeError, ValueError):
        r = None
    try:
        w = int(float(t.get("tray_weight")))
    except (TypeError, ValueError):
        w = None
    return (r if r is not None and 0 <= r <= 100 else None), (w if w and w > 0 else None)


def _remain_fields(t: dict) -> dict:
    pct, weight = _remain(t)
    return {"remain": pct, "spool_g": weight, "remain_g": round(weight * pct / 100) if pct is not None and weight else None}


def trays(rs: dict, model: str) -> list[dict]:
    out = []
    ams = rs.get("ams") or {}
    for unit in ams.get("ams") or []:
        aid = int(_num(unit.get("id")))
        for t in unit.get("tray") or []:
            sid = int(_num(t.get("id")))
            ttype = t.get("tray_type") or ""
            out.append({
                "ams_id": aid, "slot_id": sid, "label": tray_label(aid, sid, model),
                "type": ttype, "color": _color(t.get("tray_color")), "idx": t.get("tray_info_idx", ""),
                "sub_brand": t.get("tray_sub_brands", ""), "loaded": bool(ttype),
                "rfid": (t.get("tag_uid") or "0").strip("0") != "",
                "humidity": unit.get("humidity"), "kind": "ams",
                **_remain_fields(t),
            })
    ext = []
    for v in rs.get("vir_slot") or []:          # serie X / H / P2
        ext.append((int(_num(v.get("id"), 255)), v))
    if not ext and rs.get("vt_tray"):            # serie P1 / A1
        ext.append((255, rs["vt_tray"]))
    for aid, v in ext:
        if aid not in (254, 255):
            aid = 255
        ttype = v.get("tray_type") or ""
        out.append({
            "ams_id": aid, "slot_id": 0, "label": tray_label(aid, 0, model), "type": ttype,
            "color": _color(v.get("tray_color")), "idx": v.get("tray_info_idx", ""),
            "sub_brand": v.get("tray_sub_brands", ""), "loaded": bool(ttype), "rfid": False,
            "humidity": None, "kind": "external",
            **_remain_fields(v),
        })
    return out


# modelli con sensore di temperatura della camera (sugli altri, ad es. P1S, il valore riportato non è reale)
CHAMBER_SENSOR = {"X1", "X1C", "X1E", "H2D", "H2D PRO", "H2DPRO", "H2S", "H2C", "X2D"}


def chamber(rs: dict, model: str) -> tuple[float | None, int]:
    """(temperatura attuale, obiettivo) della camera; None se la stampante non la misura.
    Le stampanti con camera riscaldata (es. H2D) la riportano in device.ctc.info.temp, compressa
    come quella degli estrusori (bit 0–15 attuale, 16–31 obiettivo)."""
    ctc = ((rs.get("device") or {}).get("ctc") or {}).get("info") or {}
    if ctc.get("temp") is not None:
        packed = int(_num(ctc.get("temp")))
        return float(packed & 0xFFFF), (packed >> 16) & 0xFFFF
    if model in CHAMBER_SENSOR and rs.get("chamber_temper") is not None:
        return round(_num(rs.get("chamber_temper")), 1), 0
    return None, 0


# ---------------------------------------------------------------- ugelli e AMS
NOZZLE_FLOW = {"S": "standard", "H": "high", "U": "tpu"}
NOZZLE_MATERIAL = {"00": "stainless", "01": "hardened", "05": "tungsten"}
LEGACY_MATERIAL = {"stainless_steel": "stainless", "hardened_steel": "hardened", "tungsten_carbide": "tungsten"}


def nozzle_kind(code: str | None) -> dict:
    """Decodifica device.nozzle.info.type (API par. 4.1.2), formato H[Byte1][Byte2-3]:
    "HS00" = hotend, flusso standard, acciaio inox; "HH01" = alto flusso, acciaio temprato; ecc.
    Accetta anche i valori testuali delle stampanti a ugello singolo ("hardened_steel")."""
    code = str(code or "").strip()
    up = code.upper()
    m = re.match(r"^H([A-Z])(\d{2})", up)      # tollera suffissi (es. "HH01-0.4") e minuscole
    if m:
        return {"code": code, "flow": NOZZLE_FLOW.get(m.group(1)), "material": NOZZLE_MATERIAL.get(m.group(2))}
    return {"code": code, "flow": None, "material": LEGACY_MATERIAL.get(code.lower())}


# tipo di unità AMS (bit 0–3 del campo "info", come nel protocollo delle stampanti Bambu)
AMS_KIND = {1: "AMS", 2: "AMS Lite", 3: "AMS 2 Pro", 4: "AMS HT"}


def _ams_info(unit: dict) -> tuple[str | None, int | None]:
    """(tipo di AMS, ugello a cui è collegato) dal campo "info" esadecimale dell'unità.
    Il campo non è documentato nelle API del Hub v1.0.0: se manca, il collegamento resta sconosciuto."""
    raw = unit.get("info")
    if raw in (None, ""):
        return None, None
    try:
        v = int(str(raw), 16)
    except ValueError:
        return None, None
    return AMS_KIND.get(v & 0xF), (v >> 8) & 0xF


def nozzles(rs: dict, model: str, ex: list[dict]) -> list[dict]:
    """Un elemento per ugello: lato, temperatura, diametro, tipo e bobine/AMS collegati."""
    dev_noz = ((rs.get("device") or {}).get("nozzle") or {}).get("info") or []
    temps = {e["id"]: e for e in ex}
    dual = model in DUAL_NOZZLE or len(ex) > 1 or len(dev_noz) > 1
    out = []
    # valori generali di report_status: usati quando il singolo ugello non comunica tipo o diametro
    gen_type, gen_diam = rs.get("nozzle_type"), _num(rs.get("nozzle_diameter")) or None
    if dev_noz:
        for n in dev_noz:
            nid = int(_num(n.get("id")))
            t = temps.get(nid, {})
            code = n.get("type") or n.get("nozzle_type") or gen_type
            out.append({"id": nid, "side": ("right" if nid == 0 else "left") if dual else "main",
                        "temp": t.get("temp"), "target": t.get("target"),
                        "diameter": _num(n.get("diameter")) or gen_diam, **nozzle_kind(code)})
    elif ex:
        for e in ex:
            out.append({"id": e["id"], "side": ("right" if e["id"] == 0 else "left") if dual else "main",
                        "temp": e["temp"], "target": e["target"], "diameter": gen_diam, **nozzle_kind(gen_type)})
    else:   # stampanti a ugello singolo (P1, A1, X1): valori classici di report_status
        out.append({"id": 0, "side": "main", "temp": round(_num(rs.get("nozzle_temper")), 1),
                    "target": round(_num(rs.get("nozzle_target_temper")), 1),
                    "diameter": _num(rs.get("nozzle_diameter")) or None, **nozzle_kind(rs.get("nozzle_type"))})
    if dual and len(out) == 2 and out[0]["side"] == "left":
        out.reverse()
    out.sort(key=lambda n: {"left": 0, "main": 1, "right": 2}[n["side"]])   # sinistro prima, come sulla macchina
    return out


def ams_units(rs: dict, model: str, noz: list[dict]) -> list[dict]:
    """Unità AMS e bobine esterne, con l'ugello a cui sono collegate quando la stampante lo comunica."""
    single = len(noz) == 1
    out = []
    for unit in (rs.get("ams") or {}).get("ams") or []:
        aid = int(_num(unit.get("id")))
        kind, nid = _ams_info(unit)
        if kind is None:
            kind = "AMS HT" if 128 <= aid <= 135 else "AMS"
        hum_raw = unit.get("humidity_raw")
        out.append({"ams_id": aid, "kind": kind,
                    "letter": chr(65 + aid - 128) if 128 <= aid <= 135 else chr(65 + aid),
                    "nozzle": noz[0]["id"] if single else nid,
                    "humidity": int(_num(hum_raw)) if hum_raw not in (None, "") else None,
                    "temp": _num(unit.get("temp")) or None})
    return out


def external_nozzle(ams_id: int, noz: list[dict]) -> int | None:
    if len(noz) == 1:
        return noz[0]["id"]
    return 0 if ams_id == 255 else 1 if ams_id == 254 else None


def fan_pct(v) -> int | None:
    """Velocità ventola: le stampanti Bambu usano una scala 0–15; valori maggiori sono già percentuali."""
    if v in (None, ""):
        return None
    n = _num(v)
    return int(min(100, round(n / 15 * 100 / 10) * 10 if n <= 15 else n))


def extruders(rs: dict) -> list[dict]:
    info = ((rs.get("device") or {}).get("extruder") or {}).get("info") or []
    res = []
    for e in info:
        packed = int(_num(e.get("temp")))
        res.append({"id": e.get("id"), "temp": packed & 0xFFFF, "target": (packed >> 16) & 0xFFFF})
    return res


def normalize(d: dict) -> dict:
    rs = d.get("report_status") or {}
    model = (d.get("dev_model") or "").upper()
    state = (rs.get("gcode_state") or "UNKNOWN").upper()
    up = d.get("upgrade_info") or {}
    ex = extruders(rs)
    noz = nozzles(rs, model, ex)
    tr = trays(rs, model)
    for t in tr:
        if t["kind"] == "external":
            t["nozzle"] = external_nozzle(t["ams_id"], noz)
    return {
        "sn": d.get("dev_sn"),
        "model": model,
        "name": d.get("name") or d.get("dev_sn"),
        "ip": d.get("dev_ip", ""),
        "online": bool(d.get("online")),
        "mqtt": int(_num(d.get("mqtt_status"))),
        "state": state,
        "active": state in ACTIVE_STATES,
        "progress": int(_num(rs.get("mc_percent"))),
        "remaining_min": int(_num(rs.get("mc_remaining_time"))),
        "prepare_percent": int(_num(rs.get("gcode_file_prepare_percent"))),
        "layer": int(_num(rs.get("layer_num"))),
        "total_layers": int(_num(rs.get("total_layer_num"))),
        "task_name": rs.get("subtask_name") or "",
        "task_id": str(rs.get("task_id") or ""),
        "subtask_id": str(rs.get("subtask_id") or ""),
        "nozzle": round(_num(rs.get("nozzle_temper")), 1),
        "nozzle_target": round(_num(rs.get("nozzle_target_temper")), 1),
        "bed": round(_num(rs.get("bed_temper")), 1),
        "bed_target": round(_num(rs.get("bed_target_temper")), 1),
        "chamber": chamber(rs, model)[0],
        "chamber_target": chamber(rs, model)[1],
        "extruders": ex,
        "speed_lvl": int(_num(rs.get("spd_lvl"))),
        "wifi": rs.get("wifi_signal", ""),
        "nozzle_diameter": rs.get("nozzle_diameter", ""),
        "nozzle_type": rs.get("nozzle_type", ""),
        "ams_busy": (int(_num(rs.get("ams_status"))) >> 8) != 0,
        "trays": tr,
        "nozzles": noz,
        "ams_units": ams_units(rs, model, noz),
        "fans": {"part": fan_pct(rs.get("cooling_fan_speed")), "aux": fan_pct(rs.get("big_fan1_speed")),
                 "chamber": fan_pct(rs.get("big_fan2_speed"))},
        "hms": [hms_code(h) for h in rs.get("hms") or []],
        "print_error": print_error_code(rs.get("print_error")),
        "firmware": up.get("current_ver", ""),
        "upgrade_state": up.get("upgrade_state", ""),
        "liveview_time": d.get("liveview_time", ""),
        "dual_nozzle": model in DUAL_NOZZLE or len(ex) > 1,
        "last_cmd_error": d.get("latest_print_cmd_send_fail_reason", ""),
        "cmd_retry": int(_num(d.get("print_cmd_send_retry_cnt"))),
        "fw_min": firmware_below_min(model, up.get("current_ver", "")),
    }


def is_ready(p: dict) -> bool:
    """Pronta a ricevere una nuova stampa senza intervento umano."""
    return (p["online"] and p["mqtt"] == 2 and p["state"] == "IDLE"
            and p["upgrade_state"] not in ("DOWNLOADING", "UPGRADING"))


def _rgb(hexcol: str):
    h = (hexcol or "#888888").lstrip("#")[:6].ljust(6, "8")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _dist(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


def _slot_name(label: str) -> str:
    return {"EXT": _("Esterna"), "EXT_R": _("Esterna dx"), "EXT_L": _("Esterna sx")}.get(label, label)


def auto_map(filaments: list[dict], printer: dict) -> tuple[list[dict] | None, list[str]]:
    """Restituisce (filament_slot, avvisi). filament_slot è None se manca un abbinamento."""
    loaded = [t for t in printer["trays"] if t["loaded"]]
    warnings, used = [], set()
    if not filaments:
        return [{"ams_id": 255, "slot_id": 0}], warnings
    size = max(f["id"] for f in filaments)
    slots = [{"ams_id": 255, "slot_id": 255} for _ in range(size)]
    for f in filaments:
        cands = [t for t in loaded if t["type"].upper() == f["type"].upper()]
        if printer["dual_nozzle"] and f.get("extruder"):
            # slicer: estrusore 1 = sinistro, 2 = destro; Hub: ugello 1 = sinistro, 0 = destro
            want = 1 if f["extruder"] == 1 else 0
            unit_noz = {u["ams_id"]: u.get("nozzle") for u in printer.get("ams_units", [])}
            def side(t):
                return t.get("nozzle") if t["kind"] == "external" else unit_noz.get(t["ams_id"])
            known = [t for t in cands if side(t) == want]
            # se il collegamento di qualche AMS non è comunicato, si escludono solo le bobine sicuramente sbagliate
            cands = known or [t for t in cands if side(t) is None]
        if not cands:
            warnings.append(_("Filamento {n} ({type}) non caricato", n=f["id"], type=f["type"]))
            return None, warnings
        cands.sort(key=lambda t: ((t["ams_id"], t["slot_id"]) in used,
                                  t["idx"] != f["tray_info_idx"], _dist(t["color"], f["color"])))
        best = cands[0]
        if _dist(best["color"], f["color"]) > 120:
            warnings.append(_("Filamento {n}: colore diverso ({slot})", n=f["id"], slot=_slot_name(best["label"])))
        used.add((best["ams_id"], best["slot_id"]))
        slots[f["id"] - 1] = {"ams_id": best["ams_id"], "slot_id": best["slot_id"]}
    return slots, warnings


def _is_auto(s) -> bool:
    return not s or bool(s.get("auto")) or s.get("ams_id") is None


def resolve_slots(slots: list | None, filaments: list[dict], printer: dict) -> tuple[list[dict] | None, list[str]]:
    """Unisce gli slot scelti a mano con quelli "automatici" ({"auto": true}), assegnati sulla stampante
    al momento dell'avvio. slots=None significa tutto automatico. Restituisce (slot, avvisi);
    slot è None se un filamento automatico non è caricato sulla stampante."""
    if not slots:
        return auto_map(filaments, printer)
    autos = [f for f in filaments if f["id"] - 1 >= len(slots) or _is_auto(slots[f["id"] - 1])]
    if not autos:
        return slots, []
    found, warnings = auto_map(autos, printer)
    if found is None:
        return None, warnings
    size = max(len(slots), len(found), max(f["id"] for f in filaments))
    out = []
    for i in range(size):
        mine = slots[i] if i < len(slots) else None
        if mine and not _is_auto(mine):
            out.append({"ams_id": mine["ams_id"], "slot_id": mine["slot_id"]})
        elif i < len(found):
            out.append(found[i])
        else:
            out.append({"ams_id": 255, "slot_id": 255})
    return out, warnings

