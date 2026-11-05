"""Lettura dei metadati da un file .gcode "normale" (PrusaSlicer, SuperSlicer, OrcaSlicer, Cura).

Gli slicer scrivono tempo stimato, filamento usato, tipi e colori e una miniatura PNG nei commenti,
all'inizio o alla fine del file. Si leggono solo le prime e le ultime centinaia di KB, così anche
file molto grandi si analizzano in un attimo sul Raspberry Pi. Il risultato ha la stessa forma
usata per i .gcode.3mf (un "piatto" unico), così archivio, coda e storico funzionano uguale.
"""
from __future__ import annotations

import base64
import re
import struct
import zlib
from pathlib import Path

GCODE_EXT = (".gcode", ".gco", ".g", ".bgcode")
CHUNK = 400 * 1024


def is_gcode(name: str) -> bool:
    n = name.lower()
    return n.endswith(GCODE_EXT) and not n.endswith(".gcode.3mf")


def _read_ends(path: Path) -> tuple[str, str]:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(CHUNK)
        if size > CHUNK * 2:
            fh.seek(size - CHUNK)
        tail = fh.read() if size > CHUNK else b""
    return head.decode("utf-8", "replace"), tail.decode("utf-8", "replace")


def _duration(text: str) -> int:
    """'1d 2h 3m 4s' oppure '2h 3m' → secondi."""
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def _settings(text: str) -> dict[str, str]:
    """Righe '; chiave = valore' (PrusaSlicer, SuperSlicer, OrcaSlicer)."""
    out = {}
    for m in re.finditer(r"^;\s*([A-Za-z_ \[\]()]+?)\s*=\s*(.*)$", text, re.M):
        out.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    return out


def _floats(value: str | None) -> list[float]:
    out = []
    for part in re.split(r"[;,]", value or ""):
        try:
            out.append(float(part.strip()))
        except ValueError:
            pass
    return out


def _bgcode_blocks(path: Path):
    """Blocchi di metadati e miniature di un G-code binario (formato libbgcode di Prusa), fino al primo blocco di G-code.
    Restituisce (testo dei metadati, [miniature PNG con area])."""
    texts, thumbs = [], []
    with open(path, "rb") as fh:
        head = fh.read(10)
        if head[:4] != b"GCDE":
            return "", []
        checksum = struct.unpack("<H", head[8:10])[0]
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            btype, comp, usize = struct.unpack("<HHI", hdr)
            csize = struct.unpack("<I", fh.read(4))[0] if comp else usize
            if btype == 1:                      # inizio del G-code: i metadati sono finiti
                break
            params = fh.read(6 if btype == 5 else 2)
            data = fh.read(csize)
            if checksum == 1:
                fh.read(4)
            if comp == 1:
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    continue
            elif comp != 0:                     # heatshrink: raro per i metadati, si salta
                continue
            if btype == 5:
                fmt, w, h = struct.unpack("<HHH", params)
                if fmt == 0 and data[:8] == b"\x89PNG\r\n\x1a\n":
                    thumbs.append((w * h, data))
            elif btype in (0, 2, 3, 4):
                texts.extend("; " + line.replace("=", " = ", 1) for line in data.decode("utf-8", "replace").splitlines() if "=" in line)
    return "\n".join(texts), thumbs


def thumbnail_png(path: Path) -> bytes | None:
    """La miniatura PNG più grande tra quelle scritte dallo slicer ('; thumbnail begin WxH len')."""
    if str(path).lower().endswith(".bgcode"):
        thumbs = _bgcode_blocks(Path(path))[1]
        return max(thumbs)[1] if thumbs else None
    head, tail = _read_ends(path)
    best, best_px = None, 0
    for text in (head, tail):
        for m in re.finditer(r"; thumbnail(?:_PNG)? begin (\d+)x(\d+) \d+\n(.*?); thumbnail(?:_PNG)? end", text, re.S):
            px = int(m.group(1)) * int(m.group(2))
            data = "".join(line.lstrip("; ").strip() for line in m.group(3).splitlines())
            try:
                png = base64.b64decode(data)
            except ValueError:
                continue
            if png[:8] == b"\x89PNG\r\n\x1a\n" and px > best_px:
                best, best_px = png, px
    return best


def parse_gcode(path: str | Path) -> dict:
    path = Path(path)
    if path.name.lower().endswith(".bgcode"):
        text = _bgcode_blocks(path)[0]
        if not text:
            raise ValueError("file .bgcode non valido")
    else:
        head, tail = _read_ends(path)
        text = head + "\n" + tail
    st = _settings(text)
    # tempo stimato
    secs = 0
    for key in ("estimated printing time (normal mode)", "estimated printing time", "model printing time"):
        if st.get(key):
            secs = _duration(st[key])
            break
    if not secs:
        m = re.search(r"^;\s*TIME:\s*(\d+)", text, re.M)               # Cura
        if m:
            secs = int(m.group(1))
    # filamenti
    grams = _floats(st.get("filament used [g]") or st.get("total filament used [g]"))
    mm = _floats(st.get("filament used [mm]"))
    types = [t.strip() for t in (st.get("filament_type") or "").split(";") if t.strip()]
    colors = [c.strip() for c in (st.get("filament_colour") or st.get("extruder_colour") or "").split(";") if c.strip()]
    if not grams:
        m = re.search(r"^;\s*Filament used:\s*([\d.]+)m", text, re.M)  # Cura (metri)
        if m:
            mm = [float(m.group(1)) * 1000]
    count = max(len(grams), len(mm), len(types), 1)
    fils = []
    for i in range(count):
        g = grams[i] if i < len(grams) else 0.0
        meters = (mm[i] / 1000) if i < len(mm) else 0.0
        if count > 1 and not g and not meters:
            continue                                  # estrusore non usato
        fils.append({"id": i + 1, "type": types[i] if i < len(types) else (types[0] if types else "?"),
                     "color": (colors[i] if i < len(colors) else "#888888").upper() or "#888888",
                     "tray_info_idx": "", "used_g": round(g, 2), "used_m": round(meters, 2),
                     "extruder": None, "tool": i})
    weight = round(sum(f["used_g"] for f in fils), 2)
    slicer = ""
    m = re.search(r"^;\s*generated by\s+(.+)$", text, re.M | re.I) or re.search(r"^;\s*Generated with\s+(.+)$", text, re.M)
    if m:
        slicer = m.group(1).strip()[:80]
    return {
        "kind": "gcode",
        "slicer_version": slicer,
        "printer_model_id": st.get("printer_model", ""),
        "plates": [{"index": 1, "gcode": path.name, "has_gcode": True, "prediction_s": secs, "weight_g": weight,
                    "printer_model_id": st.get("printer_model", ""), "nozzle_diameters": st.get("nozzle_diameter", ""),
                    "filaments": fils, "has_thumb": thumbnail_png(path) is not None, "objects": []}],
    }
