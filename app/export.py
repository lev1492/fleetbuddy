"""Esportazione delle statistiche in Excel (.xlsx).

I totali e le percentuali sono formule, così il file resta coerente se qualcuno
modifica o filtra i dati in Excel. Excel ricalcola tutto all'apertura.
"""
from __future__ import annotations

import io
import time
from collections import defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import APP_NAME, __version__
from .i18n import tr

FONT = "Arial"
F_BASE = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_TITLE = Font(name=FONT, size=14, bold=True)
F_NOTE = Font(name=FONT, size=9, italic=True, color="5B6872")
FILL_HEAD = PatternFill("solid", start_color="1B2228")
FILL_TOT = PatternFill("solid", start_color="E9EDF0")
TOP = Border(top=Side(style="thin", color="1B2228"))
STATUS_IT = {"finished": "Completata", "failed": "Fallita", "cancelled": "Annullata", "to_confirm": "Da confermare",
             "running": "In corso", "unknown": "Interrotta"}
SHEET_LIMIT = 31   # Excel accetta al massimo 31 caratteri nel nome di un foglio


def _header(ws, row: int, labels: list[str], widths: list[int]):
    for i, (lab, w) in enumerate(zip(labels, widths), start=1):
        c = ws.cell(row=row, column=i, value=lab)
        c.font, c.fill = F_HEAD, FILL_HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 28
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _style_rows(ws, first: int, last: int, ncols: int):
    for r in range(first, last + 1):
        for c in range(1, ncols + 1):
            ws.cell(row=r, column=c).font = F_BASE


def _total_row(ws, row: int, ncols: int, label: str):
    ws.cell(row=row, column=1, value=label)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font, cell.fill, cell.border = F_BOLD, FILL_TOT, TOP


def build_stats_xlsx(jobs: list[dict], *, days: int, scope: str, user: str, lang: str = "it",
                     filaments: list[dict] | None = None) -> bytes:
    now = time.time()
    T = lambda text, **kw: tr(lang, text, **kw)  # noqa: E731
    sh = lambda text: T(text)[:SHEET_LIMIT].replace("'", "")  # noqa: E731
    wb = Workbook()

    # ------------------------------------------------------------ Dettaglio stampe
    det = wb.active
    det.title = sh("Dettaglio stampe")
    cols = [T("Avvio"), T("Fine"), T("Stampante"), T("Seriale"), T("Lavoro"), T("Avviata da"), T("Durata (h)"), T("Filamento (g)"), T("Esito"), T("Avanzamento"), T("Errore")]
    _header(det, 1, cols, [17, 17, 22, 18, 34, 14, 11, 13, 13, 13, 12])
    for i, j in enumerate(sorted(jobs, key=lambda x: x["started_at"]), start=2):
        end = j.get("ended_at")
        det.cell(row=i, column=1, value=datetime.fromtimestamp(j["started_at"])).number_format = "dd/mm/yyyy hh:mm"
        if end:
            det.cell(row=i, column=2, value=datetime.fromtimestamp(end)).number_format = "dd/mm/yyyy hh:mm"
        det.cell(row=i, column=3, value=j.get("printer_name") or "")
        det.cell(row=i, column=4, value=j.get("printer_sn") or "")
        det.cell(row=i, column=5, value=j.get("task_name") or "")
        det.cell(row=i, column=6, value=j.get("started_by_name") or "")
        det.cell(row=i, column=7, value=round(((end or now) - j["started_at"]) / 3600, 3)).number_format = "0.00"
        det.cell(row=i, column=8, value=round(j.get("filament_g") or 0, 1)).number_format = "#,##0.0"
        det.cell(row=i, column=9, value=T(STATUS_IT.get(j["status"], j["status"])))
        det.cell(row=i, column=10, value=(j.get("progress") or 0) / 100).number_format = "0%"
        det.cell(row=i, column=11, value=j.get("error") or "")
    last_det = max(1, len(jobs) + 1)
    _style_rows(det, 2, last_det, len(cols))
    det.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last_det}"

    # ------------------------------------------------------------ Per stampante
    per: dict[str, dict] = defaultdict(lambda: {"name": "", "n": 0, "ok": 0, "ko": 0, "canc": 0, "h": 0.0, "g": 0.0})
    for j in jobs:
        p = per[j.get("printer_sn") or "?"]
        p["name"] = j.get("printer_name") or p["name"]
        p["n"] += 1
        p["ok"] += j["status"] == "finished"
        p["ko"] += j["status"] == "failed"
        p["canc"] += j["status"] == "cancelled"
        p["h"] += ((j.get("ended_at") or now) - j["started_at"]) / 3600
        p["g"] += j.get("filament_g") or 0
    ps = wb.create_sheet(sh("Per stampante"))
    pcols = [T("Stampante"), T("Seriale"), T("Stampe"), T("Riuscite"), T("Fallite"), T("Annullate"), T("Tasso di successo"), T("Ore di stampa"), T("Filamento (g)")]
    _header(ps, 1, pcols, [24, 18, 10, 10, 10, 11, 12, 12, 13])
    rows = sorted(per.items(), key=lambda kv: -kv[1]["n"])
    for i, (sn, p) in enumerate(rows, start=2):
        for c, v in enumerate([p["name"], sn, p["n"], p["ok"], p["ko"], p["canc"]], start=1):
            ps.cell(row=i, column=c, value=v)
        ps.cell(row=i, column=7, value=f"=IF(C{i}=0,0,D{i}/C{i})").number_format = "0.0%"
        ps.cell(row=i, column=8, value=round(p["h"], 2)).number_format = "#,##0.0"
        ps.cell(row=i, column=9, value=round(p["g"], 1)).number_format = "#,##0"
    last_p = len(rows) + 1
    _style_rows(ps, 2, last_p, len(pcols))
    tp = last_p + 1
    _total_row(ps, tp, len(pcols), T("Totale"))
    for c in (3, 4, 5, 6, 8, 9):
        L = get_column_letter(c)
        ps.cell(row=tp, column=c, value=f"=SUM({L}2:{L}{last_p})" if rows else 0)
    ps.cell(row=tp, column=7, value=f"=IF(C{tp}=0,0,D{tp}/C{tp})")
    ps.cell(row=tp, column=7).number_format = "0.0%"
    ps.cell(row=tp, column=8).number_format = "#,##0.0"
    ps.cell(row=tp, column=9).number_format = "#,##0"

    # ------------------------------------------------------------ Per giorno
    daily: dict[str, dict] = defaultdict(lambda: {"n": 0, "ok": 0, "g": 0.0})
    for j in jobs:
        d = datetime.fromtimestamp(j["started_at"]).date()
        daily[d]["n"] += 1
        daily[d]["ok"] += j["status"] == "finished"
        daily[d]["g"] += j.get("filament_g") or 0
    gs = wb.create_sheet(sh("Per giorno"))
    gcols = [T("Data"), T("Stampe"), T("Riuscite"), T("Non riuscite"), T("Tasso di successo"), T("Filamento (g)")]
    _header(gs, 1, gcols, [13, 10, 10, 12, 12, 13])
    drows = sorted(daily.items())
    for i, (d, v) in enumerate(drows, start=2):
        gs.cell(row=i, column=1, value=d).number_format = "dd/mm/yyyy"
        gs.cell(row=i, column=2, value=v["n"])
        gs.cell(row=i, column=3, value=v["ok"])
        gs.cell(row=i, column=4, value=f"=B{i}-C{i}")
        gs.cell(row=i, column=5, value=f"=IF(B{i}=0,0,C{i}/B{i})").number_format = "0.0%"
        gs.cell(row=i, column=6, value=round(v["g"], 1)).number_format = "#,##0"
    last_g = len(drows) + 1
    _style_rows(gs, 2, last_g, len(gcols))
    tg = last_g + 1
    _total_row(gs, tg, len(gcols), T("Totale"))
    for c in (2, 3, 4, 6):
        L = get_column_letter(c)
        gs.cell(row=tg, column=c, value=f"=SUM({L}2:{L}{last_g})" if drows else 0)
    gs.cell(row=tg, column=5, value=f"=IF(B{tg}=0,0,C{tg}/B{tg})").number_format = "0.0%"
    gs.cell(row=tg, column=6).number_format = "#,##0"

    # ------------------------------------------------------------ Filamenti
    fs = wb.create_sheet(sh("Filamenti"))
    fcols = [T("Tipo"), T("Marca"), T("Colore"), T("Filamento (g)"), T("Metri"), T("Stampe")]
    _header(fs, 1, fcols, [12, 22, 12, 14, 10, 10])
    frows = filaments or []
    for i, f in enumerate(frows, start=2):
        fs.cell(row=i, column=1, value=f["type"])
        fs.cell(row=i, column=2, value=f.get("brand") or "")
        col = (f.get("color") or "").lstrip("#")[:6].upper()
        c = fs.cell(row=i, column=3, value=f"#{col}" if col else "")
        if len(col) == 6:
            c.fill = PatternFill("solid", start_color=col)
            dark = sum(int(col[k:k + 2], 16) for k in (0, 2, 4)) < 384
            c.font = Font(name=FONT, size=10, color="FFFFFF" if dark else "000000")
        fs.cell(row=i, column=4, value=f["grams"]).number_format = "#,##0.0"
        fs.cell(row=i, column=5, value=f.get("meters") or 0).number_format = "#,##0.0"
        fs.cell(row=i, column=6, value=f["prints"])
    last_f = len(frows) + 1
    _style_rows(fs, 2, last_f, 2)
    for r in range(2, last_f + 1):
        for cidx in (4, 5, 6):
            fs.cell(row=r, column=cidx).font = F_BASE
    tf = last_f + 1
    _total_row(fs, tf, len(fcols), T("Totale"))
    fs.cell(row=tf, column=4, value=f"=SUM(D2:D{last_f})" if frows else 0).number_format = "#,##0.0"
    fs.cell(row=tf, column=5, value=f"=SUM(E2:E{last_f})" if frows else 0).number_format = "#,##0.0"
    fs.cell(row=tf + 2, column=1, value=T(
        "Il consumo per tipo e colore è noto per le stampe avviate da FleetBuddy o il cui file è nell'archivio.")).font = F_NOTE

    # ------------------------------------------------------------ Riepilogo (primo foglio)
    rs = wb.create_sheet(sh("Riepilogo"), 0)
    rs.column_dimensions["A"].width = 26
    rs.column_dimensions["B"].width = 22
    rs["A1"] = T("{app} – statistiche di stampa", app=APP_NAME)
    rs["A1"].font = F_TITLE
    info = [(T("Periodo"), T("Ultimi {n} giorni", n=days)), (T("Stampanti"), scope),
            (T("Generato il"), datetime.fromtimestamp(now)), (T("Generato da"), f"{user} · {APP_NAME} v{__version__}")]
    for i, (k, v) in enumerate(info, start=3):
        rs.cell(row=i, column=1, value=k).font = F_BOLD
        c = rs.cell(row=i, column=2, value=v)
        c.font = F_BASE
        if isinstance(v, datetime):
            c.number_format = "dd/mm/yyyy hh:mm"
            c.alignment = Alignment(horizontal="left")
    ref = f"'{ps.title}'!"
    kpis = [
        (T("Stampe"), f"={ref}C{tp}", "#,##0"),
        (T("Riuscite"), f"={ref}D{tp}", "#,##0"),
        (T("Fallite"), f"={ref}E{tp}", "#,##0"),
        (T("Annullate"), f"={ref}F{tp}", "#,##0"),
        (T("Tasso di successo"), "=IF(B9=0,0,B10/B9)", "0.0%"),
        (T("Ore di stampa"), f"={ref}H{tp}", "#,##0.0"),
        (T("Filamento (kg)"), f"={ref}I{tp}/1000", "#,##0.00"),
    ]
    rs.cell(row=8, column=1, value=T("Indicatore")).font = F_HEAD
    rs.cell(row=8, column=2, value=T("Valore")).font = F_HEAD
    rs.cell(row=8, column=1).fill = rs.cell(row=8, column=2).fill = FILL_HEAD
    for i, (lab, formula, fmt) in enumerate(kpis, start=9):
        rs.cell(row=i, column=1, value=lab).font = F_BASE
        c = rs.cell(row=i, column=2, value=formula)
        c.font, c.number_format = F_BOLD, fmt
    rs.cell(row=17, column=1, value=T(
        "Durate e grammi provengono dal monitoraggio del Hub; per le stampe interrotte il filamento "
        "è stimato in proporzione all'avanzamento.")).font = F_NOTE

    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------- CSV
CSV_COLUMNS = {
    "jobs": [("Avvio", "started_at", "datetime"), ("Fine", "ended_at", "datetime"), ("Stampante", "printer_name", "text"),
             ("Seriale", "printer_sn", "text"), ("Lavoro", "task_name", "text"), ("Avviata da", "started_by_name", "text"),
             ("Durata (h)", "_hours", "num"), ("Filamento (g)", "filament_g", "num"), ("Esito", "_status", "text"),
             ("Avanzamento", "progress", "int"), ("Filamenti", "_filaments", "text"), ("Errore", "error", "text")],
    "filaments": [("Tipo", "type", "text"), ("Marca", "brand", "text"), ("Colore", "color", "text"),
                  ("Filamento (g)", "grams", "num"), ("Metri", "meters", "num"), ("Stampe", "prints", "int")],
    "printers": [("Stampante", "name", "text"), ("Seriale", "printer_sn", "text"), ("Stampe", "n", "int"),
                 ("Riuscite", "ok", "int"), ("Fallite", "ko", "int"), ("Annullate", "cancelled", "int"),
                 ("Ore di stampa", "_hours", "num"), ("Filamento (g)", "grams", "num")],
    "daily": [("Data", "day", "text"), ("Stampe", "n", "int"), ("Riuscite", "ok", "int"), ("Filamento (g)", "grams", "num")],
}


def build_csv(kind: str, rows: list[dict], lang: str = "it") -> bytes:
    """CSV pronto per Excel/LibreOffice: in inglese virgola e punto decimale, nelle altre lingue
    punto e virgola e virgola decimale (le impostazioni regionali di quei paesi)."""
    import csv
    sep, dec = (",", ".") if lang == "en" else (";", ",")
    now = time.time()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n")
    cols = CSV_COLUMNS[kind]
    w.writerow([tr(lang, label) for label, _, _ in cols])
    for r in rows:
        r = dict(r)
        if "started_at" in r:
            r["_hours"] = ((r.get("ended_at") or now) - r["started_at"]) / 3600
            r["_status"] = tr(lang, STATUS_IT.get(r.get("status"), r.get("status") or ""))
            r["_filaments"] = "; ".join(f"{f.get('type', '')} {f.get('color', '')} {f.get('grams', 0)} g"
                                        + (" ≈" if f.get("source") == "ams" else "") for f in r.get("filaments") or [])
        elif "secs" in r:
            r["_hours"] = (r.get("secs") or 0) / 3600
        out = []
        for _, key, kind_ in cols:
            v = r.get(key)
            if v is None or v == "":
                out.append("")
            elif kind_ == "datetime":
                out.append(datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M"))
            elif kind_ == "num":
                out.append(f"{float(v):.2f}".replace(".", dec))
            elif kind_ == "int":
                out.append(str(int(v)))
            else:
                out.append(str(v))
        w.writerow(out)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")   # BOM: Excel riconosce gli accenti
