"""File di stampa a resina: .goo (Elegoo) e .ctb (Chitubox) per le stampanti Elegoo SDCP (Saturn, Mars, Jupiter…).

Si leggono solo le intestazioni: macchina, strati, tempo di stampa, volume e peso della resina, anteprima.
Disposizione dei campi ricavata dalle specifiche pubbliche dei formati (documentate anche dal progetto UVtools);
codice scritto per Fabbrix Farm Manager.

  GOO  intestazione big-endian, "V3.0" + magic 07 00 00 00 44 4C 50 00; anteprime 116x116 e 290x290 RGB565.
  CTB  intestazione little-endian, magic 0x12FD0019 (cbddlp), 0x12FD0086 (ctb), 0x12FD0106 (ctb v4);
       anteprime RGB15 compresse (bit 0x20 = ripetizione). Il CTB cifrato (0x12FD0107, Chitubox 1.9+) ha le
       impostazioni cifrate: si accetta e si stampa, ma tempo, peso e anteprima non sono leggibili.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

RESIN_EXT = (".ctb", ".goo", ".cbddlp")
GOO_MAGIC = bytes([0x07, 0x00, 0x00, 0x00, 0x44, 0x4C, 0x50, 0x00])
CTB_MAGICS = {0x12FD0019: "cbddlp", 0x12FD0086: "ctb", 0x12FD0106: "ctb v4", 0xFF220810: "ctb v4"}
CTB_ENCRYPTED = 0x12FD0107


def is_resin(name: str) -> bool:
    return name.lower().endswith(RESIN_EXT)


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def _png(width: int, height: int, rgb: bytes) -> bytes:
    """PNG RGB senza dipendenze esterne."""
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------------- GOO (Elegoo)
_GOO_SMALL, _GOO_BIG = 116, 290
_GOO_TEXT = 4 + 8 + 32 + 24 + 24 + 32 + 32 + 32           # versione, magic, 6 testi
_GOO_BIG_AT = _GOO_TEXT + 6 + _GOO_SMALL * _GOO_SMALL * 2 + 2
_GOO_PARAMS_AT = _GOO_BIG_AT + _GOO_BIG * _GOO_BIG * 2 + 2


def _goo(data: bytes) -> dict:
    o = 4 + 8
    machine = _cstr(data[o + 32 + 24 + 24:o + 32 + 24 + 24 + 32])
    p = _GOO_PARAMS_AT
    layers, = struct.unpack_from(">I", data, p)
    p += 4 + 2 + 2 + 1 + 1 + 4 * 5          # strati, risoluzione, specchiature, dimensioni, altezza strato, esposizione
    p += 1 + 4 * 7 + 4                       # modalità di attesa, 7 tempi, esposizione degli strati di base
    p += 4 + 4 * 16                          # strati di base, 16 parametri di sollevamento
    p += 2 + 2 + 1                           # potenza della luce (base e normale), impostazioni per strato
    print_s, volume, grams, cost = struct.unpack_from(">Ifff", data, p)
    return {"format": "goo", "machine": machine, "layers": layers, "print_s": print_s,
            "volume_ml": round(volume / 1000 if volume > 10000 else volume, 2),   # mm³ oppure ml, a seconda del programma
            "grams": round(grams, 1), "encrypted": False}


def _goo_preview(data: bytes) -> bytes | None:
    n = _GOO_BIG * _GOO_BIG
    px = data[_GOO_BIG_AT:_GOO_BIG_AT + n * 2]
    if len(px) < n * 2:
        return None
    rgb = bytearray(n * 3)
    for i in range(n):
        v = (px[2 * i] << 8) | px[2 * i + 1]                # RGB565 big-endian
        rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2] = ((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3
    return _png(_GOO_BIG, _GOO_BIG, bytes(rgb))


# ---------------------------------------------------------------------- CTB (Chitubox)
def _ctb(data: bytes, magic: int) -> dict:
    (bed_x, bed_y, bed_z) = struct.unpack_from("<fff", data, 8)
    layers, = struct.unpack_from("<I", data, 68)
    print_s, = struct.unpack_from("<I", data, 76)
    params_at, params_size = struct.unpack_from("<II", data, 84)
    slicer_at, slicer_size = struct.unpack_from("<II", data, 104)
    volume = grams = 0.0
    if params_at and params_size >= 28 and params_at + 28 <= len(data):
        volume, grams = struct.unpack_from("<ff", data, params_at + 20)
    machine = ""
    if slicer_at and slicer_size >= 36 and slicer_at + 36 <= len(data):
        name_at, name_size = struct.unpack_from("<II", data, slicer_at + 28)
        if name_at and 0 < name_size < 256 and name_at + name_size <= len(data):
            machine = _cstr(data[name_at:name_at + name_size])
    return {"format": CTB_MAGICS[magic], "machine": machine, "layers": layers, "print_s": print_s,
            "volume_ml": round(volume, 2), "grams": round(grams, 1), "encrypted": False}


def _ctb_preview(data: bytes) -> bytes | None:
    """Anteprima grande (indirizzo a 60), con ripiego sulla piccola (indirizzo a 72)."""
    for at_field in (60, 72):
        at, = struct.unpack_from("<I", data, at_field)
        if not at or at + 16 > len(data):
            continue
        w, h, img_at, img_len = struct.unpack_from("<IIII", data, at)
        if not (0 < w <= 2000 and 0 < h <= 2000) or img_at + img_len > len(data):
            continue
        rle, rgb, i, n = data[img_at:img_at + img_len], bytearray(w * h * 3), 0, 0
        try:
            while i + 1 < len(rle) and n < w * h:
                dot = rle[i] | (rle[i + 1] << 8); i += 2
                r, g, b = ((dot >> 11) & 0x1F) << 3, ((dot >> 6) & 0x1F) << 3, (dot & 0x1F) << 3
                rep = 1
                if dot & 0x20:
                    rep += rle[i] | ((rle[i + 1] & 0x0F) << 8); i += 2
                for _ in range(min(rep, w * h - n)):
                    rgb[3 * n], rgb[3 * n + 1], rgb[3 * n + 2] = r, g, b
                    n += 1
        except IndexError:
            continue
        return _png(w, h, bytes(rgb))
    return None


# ---------------------------------------------------------------------- interfaccia comune
def _read(path: Path, size: int = _GOO_PARAMS_AT + 512) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(size)


def info(path: str | Path) -> dict:
    path = Path(path)
    head = _read(path, 512)
    if head[4:12] == GOO_MAGIC:
        return _goo(_read(path))
    magic, = struct.unpack_from("<I", head, 0)
    if magic == CTB_ENCRYPTED:
        return {"format": "ctb", "machine": "", "layers": 0, "print_s": 0, "volume_ml": 0, "grams": 0, "encrypted": True}
    if magic in CTB_MAGICS:
        return _ctb(path.read_bytes() if path.stat().st_size < 64 << 20 else _read(path, 4 << 20), magic)
    raise ValueError("non è un file .ctb o .goo riconosciuto")


def thumbnail_png(path: str | Path) -> bytes | None:
    path = Path(path)
    try:
        head = _read(path, 512)
        if head[4:12] == GOO_MAGIC:
            return _goo_preview(_read(path))
        magic, = struct.unpack_from("<I", head, 0)
        if magic in CTB_MAGICS:
            return _ctb_preview(path.read_bytes() if path.stat().st_size < 64 << 20 else _read(path, 4 << 20))
    except (OSError, struct.error, ValueError):
        pass
    return None


def parse_resin(path: str | Path) -> dict:
    """Dati del file nello stesso formato degli altri (un solo "piatto")."""
    i = info(path)
    # stesso formato dei G-code (used_g / used_m / tool), così il resto di Farm Manager li tratta allo stesso modo
    fil = [{"type": "Resina", "color": "", "used_g": i["grams"], "used_m": 0, "tool": 0, "volume_ml": i["volume_ml"]}] if i["grams"] else []
    return {"kind": "resin", "format": i["format"], "printer_model_id": i["machine"], "printer_model": i["machine"],
            "encrypted": i["encrypted"],
            "plates": [{"index": 1, "gcode": Path(path).name, "has_gcode": True, "prediction_s": i["print_s"],
                        "weight_g": i["grams"], "filaments": fil, "layers": i["layers"], "volume_ml": i["volume_ml"],
                        "has_thumb": thumbnail_png(path) is not None, "objects": []}]}


def file_thumbnail(path, meta: dict, plate: int = 1) -> bytes | None:
    """Miniatura di un file dell'archivio secondo il suo tipo: G-code, resina o 3mf."""
    kind = (meta or {}).get("kind")
    if kind == "resin":
        return thumbnail_png(path)
    if kind == "gcode":
        from .gcodemeta import thumbnail_png as g_thumb
        return g_thumb(path)
    from .threemf import thumbnail
    return thumbnail(path, plate)
