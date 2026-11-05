"""Conversione delle miniature in PNG senza librerie esterne.

RepRapFirmware (e alcuni slicer) forniscono le miniature in formato QOI ("Quite OK Image"): qui c'è
un decodificatore QOI completo e un codificatore PNG minimo, così il browser riceve sempre un PNG.
"""
from __future__ import annotations

import struct
import zlib


def qoi_to_rgba(data: bytes) -> tuple[int, int, bytearray]:
    if data[:4] != b"qoif":
        raise ValueError("non è un'immagine QOI")
    w, h, channels, _cs = struct.unpack(">IIBB", data[4:14])
    if w * h > 4096 * 4096:
        raise ValueError("immagine troppo grande")
    px = bytearray(w * h * 4)
    index = [(0, 0, 0, 0)] * 64
    r, g, b, a = 0, 0, 0, 255
    p, run = 14, 0
    end = len(data) - 8
    for o in range(0, len(px), 4):
        if run:
            run -= 1
        elif p < end:
            b1 = data[p]; p += 1
            if b1 == 0xFE:                               # QOI_OP_RGB
                r, g, b = data[p], data[p + 1], data[p + 2]; p += 3
            elif b1 == 0xFF:                             # QOI_OP_RGBA
                r, g, b, a = data[p], data[p + 1], data[p + 2], data[p + 3]; p += 4
            elif b1 >> 6 == 0:                           # QOI_OP_INDEX
                r, g, b, a = index[b1]
            elif b1 >> 6 == 1:                           # QOI_OP_DIFF
                r = (r + ((b1 >> 4) & 3) - 2) & 255
                g = (g + ((b1 >> 2) & 3) - 2) & 255
                b = (b + (b1 & 3) - 2) & 255
            elif b1 >> 6 == 2:                           # QOI_OP_LUMA
                b2 = data[p]; p += 1
                vg = (b1 & 0x3F) - 32
                r = (r + vg - 8 + ((b2 >> 4) & 15)) & 255
                g = (g + vg) & 255
                b = (b + vg - 8 + (b2 & 15)) & 255
            else:                                        # QOI_OP_RUN
                run = b1 & 0x3F
            index[(r * 3 + g * 5 + b * 7 + a * 11) % 64] = (r, g, b, a)
        px[o:o + 4] = bytes((r, g, b, a))
    return w, h, px


def rgba_to_png(w: int, h: int, px: bytes) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    stride = w * 4
    raw = b"".join(b"\x00" + bytes(px[y * stride:(y + 1) * stride]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def qoi_to_png(data: bytes) -> bytes:
    return rgba_to_png(*qoi_to_rgba(data))


def rgba_to_qoi(w: int, h: int, px: bytes) -> bytes:
    """Codificatore QOI minimo (solo RGBA e RUN), usato dai test e dal simulatore Duet."""
    out = bytearray(b"qoif" + struct.pack(">IIBB", w, h, 4, 0))
    prev, run = None, 0
    for o in range(0, len(px), 4):
        cur = bytes(px[o:o + 4])
        if cur == prev:
            run += 1
            if run == 62:
                out.append(0xC0 | 61); run = 0
            continue
        if run:
            out.append(0xC0 | (run - 1)); run = 0
        out += b"\xff" + cur
        prev = cur
    if run:
        out.append(0xC0 | (run - 1))
    return bytes(out + b"\x00" * 7 + b"\x01")
