"""Analisi dei file .gcode.3mf (sezione 5 del PDF: Metadata/slice_info.config)."""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .i18n import _


def md5_file(path: str | Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_3mf(path: str | Path) -> dict:
    """Restituisce {plates:[...], slicer_version, printer_model_id}.

    Ogni plate: index, gcode (percorso interno), has_gcode, prediction_s, weight_g,
    filaments [{id, type, color, tray_info_idx, used_g, used_m, extruder}], has_thumb.
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "Metadata/slice_info.config" not in names:
            raise ValueError(_("Serve un file .gcode.3mf esportato dallo slicer (\"Esporta G-code del piatto\"), non un progetto non sezionato"))
        root = ET.fromstring(z.read("Metadata/slice_info.config"))

    out = {"plates": [], "slicer_version": "", "printer_model_id": ""}
    for hi in root.iter("header_item"):
        if hi.get("key") == "X-BBL-Client-Version":
            out["slicer_version"] = hi.get("value", "")

    for plate in root.findall("plate"):
        meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
        idx = int(meta.get("index", "1") or 1)
        maps = [int(x) for x in (meta.get("filament_maps") or "").split() if x.isdigit()]
        nozzles = {n.get("id"): n.get("extruder_id") for n in plate.findall("nozzle")}
        fils = []
        for f in plate.findall("filament"):
            fid = int(f.get("id", "0") or 0)
            if fid in (0, 255):
                continue
            extruder = None
            if f.get("group_id") is not None and nozzles.get(f.get("group_id")):
                extruder = int(nozzles[f.get("group_id")])
            elif maps and 0 < fid <= len(maps):
                extruder = maps[fid - 1]
            fils.append({
                "id": fid,
                "type": f.get("type", ""),
                "color": (f.get("color") or "#888888").upper(),
                "tray_info_idx": f.get("tray_info_idx", ""),
                "used_g": _f(f.get("used_g")),
                "used_m": _f(f.get("used_m")),
                "extruder": extruder,   # 1 = sinistro, 2 = destro (solo doppio estrusore)
            })
        gcode = f"Metadata/plate_{idx}.gcode"
        out["printer_model_id"] = out["printer_model_id"] or meta.get("printer_model_id", "")
        out["plates"].append({
            "index": idx,
            "gcode": gcode,
            "has_gcode": gcode in names,
            "prediction_s": int(_f(meta.get("prediction"))),
            "weight_g": round(_f(meta.get("weight")) or sum(x["used_g"] for x in fils), 2),
            "printer_model_id": meta.get("printer_model_id", ""),
            "nozzle_diameters": meta.get("nozzle_diameters", ""),
            "filaments": sorted(fils, key=lambda x: x["id"]),
            "has_thumb": f"Metadata/plate_{idx}.png" in names,
            "objects": [o.get("name") for o in plate.findall("object") if o.get("skipped") != "true"],
        })
    out["plates"] = [p for p in out["plates"] if p["has_gcode"]] or out["plates"]
    if not any(p["has_gcode"] for p in out["plates"]):
        raise ValueError(_("Il 3MF non contiene G-code: esportalo come .gcode.3mf dallo slicer"))
    return out


def thumbnail(path: str | Path, plate: int) -> bytes | None:
    with zipfile.ZipFile(path) as z:
        for name in (f"Metadata/plate_{plate}.png", f"Metadata/plate_{plate}_small.png", "Metadata/plate_1.png"):
            if name in z.namelist():
                return z.read(name)
    return None
