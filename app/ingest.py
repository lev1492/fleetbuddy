"""Importazione di un file nell'archivio: dal browser, dagli slicer (API compatibile OctoPrint) o da altre sorgenti.

Riconosce il tipo di file, impacchetta il G-code Bambu Lab in .gcode.3mf (il Fleet Hub stampa solo quelli), legge i
metadati, evita i doppioni (stesso MD5) e sceglie un nome libero nella cartella.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from . import bambu_gcode, production, resinmeta
from .gcodemeta import is_gcode, parse_gcode
from .i18n import _
from .threemf import md5_file, parse_3mf

FILE_EXTS = (".gcode.3mf", ".3mf", ".bgcode", ".gcode", ".gco", ".g", ".ufp", ".ctb", ".goo", ".cbddlp")

# provenienza del file (colonna files.source) riconosciuta dallo User-Agent dello slicer
SOURCES = {"prusaslicer": "PrusaSlicer", "superslicer": "SuperSlicer", "orcaslicer": "OrcaSlicer",
           "bambustudio": "Bambu Studio", "ideamaker": "ideaMaker", "cura": "Cura"}


def source_from_agent(agent: str | None) -> str:
    a = (agent or "").lower().replace(" ", "").replace("-", "")
    return next((k for k in SOURCES if k in a), "slicer")


def source_label(source: str | None) -> str:
    return SOURCES.get(source or "", "") or ("Slicer" if source == "slicer" else "")


def file_made_for(svc, f: dict) -> dict | None:
    """Stampante per cui il file è stato preparato (se il file lo indica) e diametri degli ugelli."""
    meta = f.get("meta") or {}
    model, noz = "", []
    try:
        if meta.get("kind") not in ("gcode", "resin"):
            model, noz = production.file_printer(svc, f)          # 3mf: letto una volta e memorizzato
        model = model or meta.get("printer_model") or meta.get("printer_model_id") or ""
        if not noz:
            pl = (meta.get("plates") or [{}])[0]
            noz = [x for x in str(pl.get("nozzle_diameters") or "").replace(",", " ").split() if x]
    except Exception:  # noqa: BLE001 - un file illeggibile non blocca l'elenco
        pass
    model = str(model).strip()
    if not model:
        return None
    return {"model": model, "nozzles": sorted({f"{float(x):g}" for x in noz if str(x).replace(".", "", 1).isdigit()})}


def file_signature(svc, f: dict) -> tuple:
    """Configurazione di un file: stampante per cui è preparato, ugelli e materiali. Due file con lo stesso nome ma
    configurazione diversa (es. stessa staffa per H2D e per X1E, o in PETG e in PLA) possono stare nella stessa cartella."""
    mf = file_made_for(svc, f) or {}
    mats = sorted({str(x.get("type") or "").upper() for pl in (f.get("meta") or {}).get("plates") or []
                   for x in pl.get("filaments") or [] if x.get("type")})
    return (mf.get("model", "").lower(), tuple(mf.get("nozzles") or ()), tuple(mats))


def name_clash(svc, name: str, pid, sig: tuple, exclude: int | None = None) -> bool:
    for r in svc.db.q("SELECT id, path, meta FROM files WHERE filename=? COLLATE NOCASE AND project_id IS ? AND id IS NOT ?",
                      (name, pid, exclude)):
        if file_signature(svc, {"id": r["id"], "path": r["path"], "meta": json.loads(r["meta"] or "{}")}) == sig:
            return True
    return False


def ingest(svc, src: BinaryIO, filename: str, project_id: int | None, user_id: int | None, source: str = "web") -> dict:
    """Copia src nell'archivio. Restituisce {id, duplicate, filename, renamed, converted, project_id}.
    Un file non valido solleva ValueError con il messaggio già tradotto. Va eseguita in un thread (I/O e analisi)."""
    cfg = svc.cfg
    data = cfg.data_path
    (data / "tmp").mkdir(parents=True, exist_ok=True)
    (data / "archive").mkdir(parents=True, exist_ok=True)
    gcode_file = is_gcode(filename or "")
    low = (filename or "").lower()
    resin = resinmeta.is_resin(low)                     # .ctb / .goo / .cbddlp: stampanti a resina Elegoo
    ext = (Path(low).suffix if resin else (".bgcode" if low.endswith(".bgcode") else ".ufp" if low.endswith(".ufp") else ".gcode")
           if gcode_file else ".3mf")
    tmp = data / "tmp" / f"{uuid.uuid4().hex}{ext}"
    try:
        with tmp.open("wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        # G-code generato per una Bambu Lab: il Fleet Hub stampa solo .gcode.3mf, quindi lo si impacchetta
        converted = gcode_file and not resin and bambu_gcode.is_bambu_gcode(tmp)
        if converted:
            tmp3 = tmp.with_suffix(".3mf")
            try:
                bambu_gcode.convert(tmp, tmp3)
            except Exception as e:  # noqa: BLE001
                tmp3.unlink(missing_ok=True)
                raise ValueError(_("File non valido: {e}", e=e)) from e
            tmp.unlink(missing_ok=True)
            tmp, gcode_file, ext = tmp3, False, ".3mf"
        try:
            meta = (resinmeta.parse_resin if resin else parse_gcode if gcode_file else parse_3mf)(tmp)
        except Exception as e:  # noqa: BLE001
            raise ValueError(_("File non valido: {e}", e=e)) from e
        md5 = md5_file(tmp)
        existing = svc.db.one("SELECT id, project_id, filename FROM files WHERE md5=?", (md5,))
        if existing:
            return {"id": existing["id"], "duplicate": True, "project_id": existing["project_id"],
                    "filename": existing["filename"], "renamed": False, "converted": bool(converted)}
        rel = f"{cfg.app.data_dir}/archive/{md5}{ext}"
        tmp.rename(cfg.path(rel))
    finally:
        tmp.unlink(missing_ok=True)
    name = Path((filename or "").replace("\\", "/")).name or "senza_nome.gcode.3mf"
    if converted:                                       # plate_1.gcode → plate_1.gcode.3mf
        name = re.sub(r"\.(gcode|gco|g)$", "", name, flags=re.I) + ".gcode.3mf"
    sig = file_signature(svc, {"id": None, "path": rel, "meta": meta})
    final, n = name, 0
    while name_clash(svc, final, project_id, sig):   # stesso nome e stessa configurazione nella cartella: "1-nome", ...
        n += 1
        final = f"{n}-{name}"
    fid = svc.db.x("INSERT INTO files(md5, filename, path, size, uploaded_at, meta, project_id, uploaded_by, source) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   (md5, final, rel, cfg.path(rel).stat().st_size, time.time(), json.dumps(meta), project_id, user_id,
                    source))
    return {"id": fid, "duplicate": False, "filename": final, "renamed": final != name, "converted": bool(converted),
            "project_id": project_id}
