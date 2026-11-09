"""Cuore dell'applicazione: polling del Hub, storico stampe, coda, notifiche, bus eventi (SSE)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from . import notifier
from .config import Config
from .db import DB
from . import production
from .connectors import DirectClient, make_client
from .gcodemeta import is_gcode
from .hub_client import FleetHubClient, HubError
from .i18n import tr


def qmsg(msg: str, **params) -> str:
    """Messaggio di coda salvato come frase + parametri, tradotto dal browser."""
    return json.dumps({"msg": msg, "params": params}, ensure_ascii=False)
from .printers import auto_map, is_ready, normalize, resolve_slots

log = logging.getLogger("fleetbuddy.service")


class _NoHub:
    """Segnaposto quando il Fleet Hub non è configurato: ogni chiamata dà un errore leggibile."""
    def __init__(self, reason: str):
        self.reason = reason

    def __getattr__(self, name):
        def fail(*_a, **_k):
            raise HubError(key="Fleet Hub non configurato: {e}", params={"e": self.reason})
        return fail

def task_name_for(filename: str, plate: int, plates: int) -> str:
    """Nome del lavoro inviato al Hub: lo stesso usato per riconoscere la stampa nello storico."""
    task = Path(filename).name.replace(".gcode.3mf", "").replace(".3mf", "")
    for ext in (".bgcode", ".ufp", ".gcode", ".gco", ".g"):
        if task.lower().endswith(ext):
            task = task[: -len(ext)]
            break
    return f"{task}_plate_{plate}" if plates > 1 else task


def filament_plan(plate_meta: dict, slots: list[dict] | None = None, printer: dict | None = None) -> list[dict]:
    """Filamenti previsti dal file (slice_info), con la bobina reale su cui sono stati mappati se nota."""
    trays = {(t["ams_id"], t["slot_id"]): t for t in (printer or {}).get("trays", [])}
    plan = []
    for f in plate_meta.get("filaments", []):
        if not f.get("used_g"):
            continue
        slot = (slots or [])[f["id"] - 1] if slots and f["id"] - 1 < len(slots) else None
        tray = trays.get((slot["ams_id"], slot["slot_id"])) if slot else None
        loaded = tray if tray and tray.get("loaded") else None
        plan.append({
            "type": (loaded or {}).get("type") or f.get("type") or "?",
            "brand": (loaded or {}).get("sub_brand") or "",
            "color": (loaded or {}).get("color") or f.get("color") or "#888888",
            "tray_info_idx": (loaded or {}).get("idx") or f.get("tray_info_idx") or "",
            "grams": float(f.get("used_g") or 0), "meters": float(f.get("used_m") or 0),
            "ams_id": slot["ams_id"] if slot else None, "slot_id": slot["slot_id"] if slot else None,
        })
    return plan


def tray_levels(printer: dict) -> list[dict]:
    """Residuo delle bobine all'avvio della stampa (solo quelle per cui la stampante lo comunica)."""
    return [{k: t.get(k) for k in ("ams_id", "slot_id", "type", "color", "sub_brand", "idx", "remain", "spool_g")}
            for t in printer.get("trays", []) if t.get("loaded") and t.get("remain") is not None]


def ams_usage(start_json: str | None, printer: dict) -> list[dict]:
    """Consumo stimato confrontando il residuo (%) delle bobine a inizio e fine stampa.
    Funziona solo con bobine di cui la stampante conosce residuo e peso (tipicamente Bambu con RFID);
    la precisione è di circa l'1 % della bobina (10 g su 1 kg)."""
    try:
        start = json.loads(start_json or "[]")
    except ValueError:
        return []
    if not isinstance(start, list):
        return []
    now = {(t["ams_id"], t["slot_id"]): t for t in printer.get("trays", [])}
    out = []
    for s in start:
        cur = now.get((s["ams_id"], s["slot_id"]))
        if not cur or cur.get("remain") is None or cur.get("color") != s.get("color") or cur.get("type") != s.get("type"):
            continue   # bobina tolta o cambiata durante la stampa: impossibile stimare
        drop = (s.get("remain") or 0) - cur["remain"]
        weight = s.get("spool_g") or cur.get("spool_g")
        if 0 < drop <= 100 and weight:
            out.append({"type": s.get("type") or "?", "brand": s.get("sub_brand") or "", "color": (s.get("color") or "").upper(),
                        "idx": s.get("idx") or "", "grams": round(weight * drop / 100, 1),
                        "ams_id": s["ams_id"], "slot_id": s["slot_id"]})
    return out


# avvisi di fine stampa (frasi del catalogo traduzioni)
JOB_TOASTS = {"finished": "{printer}: stampa completata – {task}", "failed": "{printer}: stampa fallita – {task}",
              "cancelled": "{printer}: stampa annullata – {task}", "unknown": "{printer}: stampa interrotta – {task}"}
JOB_TITLES = {"finished": "{printer}: stampa completata", "failed": "{printer}: stampa fallita",
              "cancelled": "{printer}: stampa annullata", "unknown": "{printer}: stampa interrotta"}


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = DB(cfg.data_path / "fleetbuddy.db")
        try:
            self.hub = FleetHubClient(cfg) if cfg.hub.host else _NoHub("host vuoto in config.toml")
        except Exception as e:  # noqa: BLE001 - certificati mancanti: FleetBuddy funziona con le sole stampanti dirette
            log.warning("Fleet Hub non disponibile (%s): restano attive le stampanti collegate direttamente", e)
            self.hub = _NoHub(str(e))
        self.hub_printers: dict[str, dict] = {}
        self.direct: dict[str, DirectClient] = {}
        self._direct_err: dict[str, str] = {}
        self.reload_direct()
        self.printers: dict[str, dict] = {}
        self.hub_ok = False
        self.hub_error = ""
        self.hub_error_obj: HubError | None = None
        self.last_poll = 0.0
        self._subscribers: set[asyncio.Queue] = set()
        self._open_jobs = self.db.open_jobs()
        self.db.x("DELETE FROM queue WHERE status='started'")   # vecchio stato "inviata", sostituito da 'sent'
        self.raw: dict[str, dict] = {}          # ultima risposta grezza del Hub per stampante (diagnostica)
        self._cooldown: dict[str, float] = {}   # sn -> non inviare altro fino a ...
        self.stopped_by_user: set[str] = set()   # interruzioni chieste da qui -> 'annullata', non 'fallita'
        self._hms_seen: dict[str, set] = {}
        self._snap_cache: dict[str, tuple[float, bytes]] = {}
        self._thumb_cache: dict[str, tuple[float, bytes | None]] = {}
        self._last_online: dict[str, tuple[float, dict]] = {}   # ultimo stato visto online (per la pianificazione)
        self._late_at = 0.0
        self._workcal = None
        self._task: asyncio.Task | None = None
        for sub in ("archive", "snapshots", "tmp"):
            (cfg.data_path / sub).mkdir(exist_ok=True)
        self._reanalyze_archive()
        self._fix_ultimaker_volume()
        # produzione: tabelle e riallineamento dei pezzi (dopo che tutto il resto è pronto)
        production.init(self.db)
        try:
            production.reconcile(self)
        except Exception:  # noqa: BLE001 - non deve impedire l'avvio
            log.exception("Produzione: riallineamento all'avvio non riuscito")

    # ------------------------------------------------------------------ eventi
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)

    def emit(self, kind: str, data):
        msg = {"kind": kind, "data": data}      # filtrato per utente in /api/events
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)

    def toast(self, msg: str, level="info", sn: str | None = None, **params):
        """Avviso a schermo: frase italiana del catalogo + parametri, tradotta nel browser."""
        self.emit("toast", {"msg": msg, "params": params, "level": level, "sn": sn})

    @property
    def notify_lang(self) -> str:
        return (self.db.get_setting("ui_settings", {}) or {}).get("notify_lang", "it")

    def nt(self, msg: str, **params) -> str:
        return tr(self.notify_lang, msg, **params)

    def snapshot_state(self) -> dict:
        return {
            "printers": sorted(self.printers.values(), key=lambda p: p["name"].lower()),
            "hub_ok": self.hub_ok, "hub_error": self.hub_error, "last_poll": self.last_poll,
            "queue_paused": self.db.get_setting("queue_paused", False),
            "queue_count": self.db.one("SELECT COUNT(*) n FROM queue WHERE status='queued'")["n"],
        }

    # ------------------------------------------------------------------ ciclo
    def start(self):
        try:
            self.backfill_filaments()
        except Exception:  # noqa: BLE001 - non deve impedire l'avvio
            log.exception("Ricostruzione del consumo di filamento non riuscita")
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while True:
            t0 = time.time()
            try:
                hub_new = None
                if not isinstance(self.hub, _NoHub):
                    try:
                        raw = await asyncio.to_thread(self.hub.devices)
                        hub_new = {p["sn"]: p for p in (normalize(d) for d in raw) if p["sn"]}
                        self.raw.update({d.get("dev_sn"): d for d in raw if d.get("dev_sn")})
                        self.hub_ok, self.hub_error, self.hub_error_obj = True, "", None
                    except HubError as e:
                        if str(e) != self.hub_error:   # registra solo i cambi di errore, non ogni polling
                            log.warning("Polling fallito: %s", e)
                        self.hub_ok, self.hub_error, self.hub_error_obj = False, str(e), e
                else:
                    self.hub_ok, self.hub_error, self.hub_error_obj = False, "", None
                direct_new = await self._poll_direct()
                if hub_new is not None:
                    self.hub_printers = hub_new
                fresh = {**(hub_new or {}), **direct_new}
                for sn, p in fresh.items():
                    await self._track(p, self.printers.get(sn))
                self.printers = {**self.hub_printers, **direct_new}
                for sn_, p_ in self.printers.items():
                    if p_["online"]:
                        self._last_online[sn_] = (time.time(), p_)
                self.last_poll = time.time()
                await self._dispatch_queue()
                await production.dispatch(self)
                if time.time() - self._late_at > 120:
                    self._late_at = time.time()
                    await self._late_usage()
            except Exception as e:  # noqa: BLE001 - il ciclo non deve mai morire
                self.hub_ok, self.hub_error = False, f"Errore interno: {e}"
                self.hub_error_obj = HubError(key="Errore interno: {e}", params={"e": e})
                log.exception("Errore nel ciclo di polling")
            self.emit("state", self.snapshot_state())
            await asyncio.sleep(max(1.0, self.cfg.app.poll_interval - (time.time() - t0)))

    # ------------------------------------------------------------------ stampanti dirette
    def reload_direct(self):
        """(Ri)carica le stampanti collegate direttamente, dopo ogni modifica in Amministrazione."""
        from .connectors import KINDS
        rows = self.db.q("SELECT * FROM direct_printers WHERE enabled=1")
        keep = {}
        for r in rows:
            if r["kind"] not in KINDS:
                continue
            sn = f"{r['kind']}-{r['id']}"
            sig = tuple((r.get(k) or "").strip() for k in ("host", "password", "api_key", "username", "extra", "snapshot_url"))
            old = self.direct.get(sn)
            if old and getattr(old, "_sig", None) == sig:   # stessi dati di collegamento: si tiene la sessione
                old.name = r["name"] or sn
                old.brand = (r.get("brand") or "").strip() or old.default_brand    # marca e modello cambiano subito
                old.printer_model = (r.get("printer_model") or "").strip()
                keep[sn] = old
            else:
                c = make_client(r)
                c._sig = sig
                keep[sn] = c
        self.direct = keep
        # nome, marca e modello aggiornati subito nello stato mostrato (senza attendere la lettura successiva)
        for sn, c in keep.items():
            st = getattr(self, "printers", {}).get(sn)   # all'avvio l'elenco non esiste ancora
            if st:
                st.update(name=c.name, brand=c.brand, printer_model=c.printer_model or st.get("model") or "")

    async def _poll_direct(self) -> dict[str, dict]:
        async def one(c: DirectClient):
            try:
                model = await asyncio.to_thread(c.model)
                self.raw[c.sn] = model
                if self._direct_err.pop(c.sn, None):
                    log.info("%s (%s) di nuovo raggiungibile", c.name, c.base)
                return c.normalize(model)
            except Exception as e:  # noqa: BLE001
                msg = e.localized("it") if isinstance(e, HubError) else str(e)
                if self._direct_err.get(c.sn) != msg:
                    log.warning("%s (%s): %s", c.name, c.base, msg)
                self._direct_err[c.sn] = msg
                return c.normalize(None, msg)
        results = await asyncio.gather(*(one(c) for c in self.direct.values()))
        return {p["sn"]: p for p in results}

    def kind_of(self, sn: str) -> str:
        return self.direct[sn].kind if sn in self.direct else "bambu"

    async def command(self, sn: str, opt: str):
        """Pausa / ripresa / stop / piatto liberato, sulla strada giusta per il tipo di stampante."""
        c = self.direct.get(sn)
        if not c:
            return await asyncio.to_thread(self.hub.opt, sn, opt)
        fn = {"pause": c.pause, "resume": c.resume, "stop": c.stop}.get(opt)
        if not fn:
            raise HubError(key="Comando non disponibile per questa stampante")
        await asyncio.to_thread(fn)
        return {"code": 0}

    async def set_temperature(self, sn: str, target: str, value: float):
        c = self.direct.get(sn)
        if not c:
            raise HubError(key="Il Fleet Hub non permette di modificare le temperature")
        await asyncio.to_thread(c.set_temperature, target, value)

    async def set_speed(self, sn: str, pct: int):
        c = self.direct.get(sn)
        if not c:
            raise HubError(key="Il Fleet Hub non permette di modificare la velocità")
        await asyncio.to_thread(c.set_speed, pct)

    # ------------------------------------------------------------------ stampe in arrivo
    def _watch_pending(self, p: dict):
        """Il Hub accetta il comando subito, ma è la stampante a scaricare il file e partire.
        Se dopo 2 minuti non è partita, si avvisa con il motivo comunicato dal Hub, se c'è."""
        if p["active"]:
            return
        r = self.db.one("SELECT data, at FROM pending_prints WHERE printer_sn=?", (p["sn"],))
        if not r or time.time() - r["at"] < 120:
            return
        data = json.loads(r["data"])
        if data.get("warned"):
            return
        reason = p.get("last_cmd_error") or ", ".join(([p["print_error"]] if p["print_error"] else []) + p["hms"])
        log.warning("%s non ha avviato la stampa %s dopo 2 minuti (motivo dal Hub: %s, tentativi: %s)",
                    p["sn"], data.get("task_name"), reason or "nessuno", p.get("cmd_retry"))
        if reason:
            self.toast("{printer}: la stampa non è partita – {e}", "error", sn=p["sn"], printer=p["name"], e=reason)
        else:
            self.toast("{printer}: la stampa non è partita entro 2 minuti. Controlla la stampante e ⋮ → Dati tecnici.",
                       "error", sn=p["sn"], printer=p["name"])
        data["warned"] = True
        self.db.x("UPDATE pending_prints SET data=? WHERE printer_sn=?", (json.dumps(data), p["sn"]))
        q = data.get("queue") or {}
        if q.get("unit"):
            production.on_not_started(self.db, q["unit"], qmsg("non partita su {printer}: {e}", printer=p["name"], e=reason)
                                      if reason else qmsg("non partita su {printer} entro 2 minuti", printer=p["name"]))
        if q.get("id"):
            why = qmsg("non partita su {printer}: {e}", printer=p["name"], e=reason) if reason else \
                qmsg("non partita su {printer} entro 2 minuti", printer=p["name"])
            if q.get("copy"):   # una delle copie: si restituisce alla voce ancora in coda
                self.db.x("UPDATE queue SET copies=copies+1, message=? WHERE id=? AND status='queued'", (why, q["id"]))
            else:
                self.db.x("UPDATE queue SET status='error', message=? WHERE id=? AND status='sent'", (why, q["id"]))

    def _fix_ultimaker_volume(self):
        """Correzione una tantum (0.16.2): lo storico UltiMaker indica il volume (mm³), che era stato trattato come
        lunghezza di filamento da 2,85 mm (grammi × 6,38 rispetto al vero). Si ricalcola e, per le stampe non
        terminate, si proporziona all'avanzamento registrato."""
        if self.db.get_setting("fix_um_volume", False):
            return
        area = 3.14159 * (2.85 / 2) ** 2
        jobs = self.db.q("""SELECT DISTINCT j.id, j.status, j.progress FROM jobs j JOIN job_filaments f ON f.job_id=j.id
                            WHERE j.printer_sn LIKE 'ultimaker-%' AND f.source='measured'""")
        for j in jobs:
            frac = 1.0 if j["status"] == "finished" else max(0.0, min(1.0, (j["progress"] or 0) / 100))
            src = "measured" if j["status"] == "finished" else "estimate"
            self.db.x("UPDATE job_filaments SET grams=ROUND(grams / ? * ?, 2), meters=ROUND(meters * 1000 / ? / 1000 * ?, 2), "
                      "source=? WHERE job_id=? AND source='measured'", (area, frac, area, frac, src, j["id"]))
            tot = (self.db.one("SELECT SUM(grams) AS g FROM job_filaments WHERE job_id=?", (j["id"],)) or {}).get("g") or 0
            self.db.x("UPDATE jobs SET filament_g=? WHERE id=?", (round(tot, 1), j["id"]))
        if jobs:
            log.info("UltiMaker: consumo ricalcolato per %d stampe già registrate (volume in mm³)", len(jobs))
        self.db.set_setting("fix_um_volume", True)

    def _reanalyze_archive(self):
        """Quando la lettura dei G-code migliora (META_VERSION), i file già in archivio vengono rianalizzati:
        così materiali, pesi e tempi si aggiornano senza doverli ricaricare."""
        from .gcodemeta import META_VERSION, parse_gcode
        n = 0
        for f in self.db.q("SELECT id, path, filename, meta FROM files"):
            try:
                meta = json.loads(f["meta"] or "{}")
            except ValueError:
                continue
            if meta.get("kind") != "gcode" or int(meta.get("meta_version") or 1) >= META_VERSION:
                continue
            p = self.cfg.path(f["path"])
            if not p.exists():
                continue
            try:
                new = parse_gcode(p)
            except Exception:  # noqa: BLE001 - file non più leggibile: resta com'è
                log.warning("Rianalisi di %s non riuscita", f["filename"])
                continue
            self.db.x("UPDATE files SET meta=? WHERE id=?", (json.dumps(new), f["id"]))
            n += 1
        if n:
            log.info("Archivio: %d file G-code rianalizzati con la nuova lettura dei metadati", n)

    async def _late_usage(self):
        """Consumo misurato che la stampante registra dopo la fine della stampa (es. storico UltiMaker):
        per le stampe chiuse nelle ultime 12 ore senza misura, si riprova e si sostituisce la stima."""
        late = {sn: c for sn, c in self.direct.items() if getattr(c, "late_usage", False)}
        if not late:
            return
        rows = self.db.q(f"""SELECT * FROM jobs WHERE status!='running' AND ended_at > ? AND printer_sn IN ({','.join('?' * len(late))})
                             AND id NOT IN (SELECT job_id FROM job_filaments WHERE source IN ('measured', 'estimate'))""",
                         (time.time() - 12 * 3600, *late))
        for job in rows:
            try:
                snap = json.loads(job.get("start_trays") or "{}")
                snap = snap.get("usage") if isinstance(snap, dict) else None
                if not snap:
                    continue
                measured = await asyncio.to_thread(late[job["printer_sn"]].usage_since, snap, self.raw.get(job["printer_sn"]))
            except Exception:  # noqa: BLE001
                continue
            if measured:
                grams = self._record_filaments(job, 100, measured=measured)
                self.db.x("UPDATE jobs SET filament_g=? WHERE id=?", (grams, job["id"]))
                log.info("%s: consumo misurato recuperato dallo storico della stampante per %s: %s g",
                         job["printer_sn"], job["task_name"], grams)

    def workcal(self):
        """Calendario lavorativo (Amministrazione → Generali), ricreato quando le impostazioni cambiano."""
        if self._workcal is None:
            from .workcal import WorkCalendar
            try:
                self._workcal = WorkCalendar(self.db.get_setting("work_calendar", {}) or {})
            except Exception:  # noqa: BLE001 - impostazioni non valide: si lavora senza calendario
                log.exception("Calendario lavorativo non valido: ignorato")
                self._workcal = WorkCalendar({})
        return self._workcal

    def has_pending(self, sn: str) -> bool:
        """La stampante sta ancora ricevendo una stampa (inviata da meno di 30 minuti, non ancora partita
        e non già segnalata come fallita): non le si manda altro finché non parte o non si capisce che non partirà."""
        r = self.db.one("SELECT data, at FROM pending_prints WHERE printer_sn=?", (sn,))
        return bool(r) and time.time() - r["at"] < 1800 and not json.loads(r["data"]).get("warned")

    def _queue_started(self, pend: dict | None):
        """La stampa inviata dalla coda è partita: la voce esce dalla coda (resta nello storico)."""
        q = (pend or {}).get("queue") or {}
        if q.get("id") and not q.get("copy"):
            self.db.x("DELETE FROM queue WHERE id=? AND status='sent'", (q["id"],))

    def _set_pending(self, sn: str, data: dict):
        self.db.x("INSERT OR REPLACE INTO pending_prints(printer_sn, data, at) VALUES(?,?,?)",
                  (sn, json.dumps(data), time.time()))

    def _take_pending(self, sn: str, consume: bool = True) -> dict | None:
        r = self.db.one("SELECT data, at FROM pending_prints WHERE printer_sn=?", (sn,))
        if not r:
            return None
        if consume or time.time() - r["at"] > 1800:
            self.db.x("DELETE FROM pending_prints WHERE printer_sn=?", (sn,))
        return json.loads(r["data"]) if time.time() - r["at"] <= 1800 else None

    def _match_file(self, task_name: str) -> tuple[dict, dict] | None:
        """Stampa non avviata da FleetBuddy (o avviata prima di un riavvio): cerca nell'archivio
        un file con lo stesso nome del lavoro, così si conoscono comunque i filamenti usati."""
        if not task_name:
            return None
        for f in self.db.q("SELECT id FROM files ORDER BY uploaded_at DESC"):
            full = self.db.file(f["id"])
            plates = full["meta"].get("plates", [])
            for pl in plates:
                if task_name_for(full["filename"], pl["index"], len(plates)) == task_name:
                    return full, pl
        return None

    def _plan_for_job(self, pend: dict | None, task_name: str) -> dict:
        if pend:
            return pend
        m = self._match_file(task_name)
        if not m:
            return {}
        f, pl = m
        plan = filament_plan(pl)
        return {"file_id": f["id"], "plate": pl["index"], "plan": plan,
                "planned_g": round(sum(x["grams"] for x in plan), 1) or pl.get("weight_g", 0)}

    # ------------------------------------------------------------------ storico
    async def _track(self, p: dict, prev: dict | None):
        sn = p["sn"]
        job = self._open_jobs.get(sn)
        key = p["task_id"] + "/" + p["subtask_id"]

        if p["active"]:
            if job and job.get("task_id") != key:
                await self._close_job(job, p, "unknown")
                job = None
            if not job:
                pend = self._take_pending(sn)
                self._queue_started(pend)
                info = self._plan_for_job(pend, p["task_name"])
                start_info: object = tray_levels(p)
                c = self.direct.get(sn)
                if c:
                    raw = self.raw.get(sn)
                    if not info.get("plan"):
                        try:
                            plan = await asyncio.to_thread(c.job_plan, raw)
                        except Exception:  # noqa: BLE001 - dato facoltativo
                            plan = None
                        if plan:
                            info["plan"], info["planned_g"] = plan, round(sum(x["grams"] for x in plan), 1)
                            log.info("%s: consumo previsto letto dalla stampante: %s g", sn, info["planned_g"])
                    try:
                        start_info = {"usage": await asyncio.to_thread(c.usage_snapshot, raw)}
                    except Exception:  # noqa: BLE001
                        start_info = {"usage": None}
                jid = self.db.start_job(
                    printer_sn=sn, printer_name=p["name"], task_id=key,
                    task_name=info.get("task_name") or p["task_name"],
                    file_id=info.get("file_id"), plate=info.get("plate"),
                    planned_g=info.get("planned_g", 0), started_by=info.get("user_id"),
                    filaments=json.dumps(info.get("plan") or []), start_trays=json.dumps(start_info))
                self._open_jobs[sn] = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
                production.on_job_started(self.db, ((pend or {}).get("queue") or {}).get("unit"), jid, sn)
                self.toast("{printer}: stampa avviata – {task}", sn=sn, printer=p["name"], task=p["task_name"])
            else:
                upd = {"progress": p["progress"]}
                if not job.get("file_id"):
                    # il comando di stampa può arrivare al Hub dopo che la stampante ha già iniziato
                    late = self._take_pending(sn)
                    self._queue_started(late)
                    if late:
                        production.on_job_started(self.db, (late.get("queue") or {}).get("unit"), job["id"], sn)
                        upd.update(file_id=late.get("file_id"), plate=late.get("plate"),
                                   planned_g=late.get("planned_g", 0), started_by=late.get("user_id"),
                                   filaments=json.dumps(late.get("plan") or []))
                self.db.update_job(job["id"], **upd)
                job.update(upd)
        elif job:
            if p["state"] == "FINISH":
                # piatto da liberare anche dopo uno stop (es. UltiMaker: wait_cleanup con result "Aborted"):
                # fa fede l'esito comunicato dalla stampante
                lr = p.get("last_result")
                await self._close_job(job, p, lr if lr in ("cancelled", "failed") else "finished")
            elif p["state"] == "FAILED":
                await self._close_job(job, p, "failed")
            elif p["state"] == "IDLE":
                if p.get("direct") and p.get("last_result"):
                    await self._close_job(job, p, p["last_result"])
                else:
                    # senza esito comunicato si guarda l'ultimo avanzamento letto; per le dirette si stima anche dove
                    # sarebbe arrivato nell'ultimo intervallo di lettura, alla velocità con cui procedeva
                    prog = job.get("progress", 0) or 0
                    if p.get("direct"):
                        rate = prog / max(1.0, time.time() - (job.get("started_at") or time.time()))
                        prog += rate * (self.cfg.app.poll_interval + 2)
                    await self._close_job(job, p, "finished" if prog >= 99 else "cancelled")
            elif not p["online"] and prev and prev["online"]:
                pass  # resta aperto: potrebbe tornare online e continuare

        self._watch_pending(p)

        # errori / HMS nuovi
        codes = set(p["hms"]) | ({p["print_error"]} if p["print_error"] else set())
        seen = self._hms_seen.get(sn)
        if seen is not None:
            for code in codes - seen:
                kind = "error" if len(code) == 8 else "hms"
                desc = await self.describe(p["model"], code, kind)
                self.toast("{printer}: {code} {desc}", "error", sn=sn, printer=p["name"], code=code, desc=desc)
                if self.cfg.notify.on_error:
                    await self._notify("error", f"⚠️ {p['name']}", f"{code}: {desc}", p["name"])
        self._hms_seen[sn] = codes

    async def _close_job(self, job: dict, p: dict, status: str):
        if status == "failed" and p["sn"] in self.stopped_by_user:
            status = "cancelled"
        self.stopped_by_user.discard(p["sn"])
        progress = 100 if status == "finished" else max(job.get("progress") or 0, p["progress"])
        measured = None
        c = self.direct.get(p["sn"])
        if c:
            try:
                snap0 = json.loads(job.get("start_trays") or "{}")
                measured = await asyncio.to_thread(c.usage_since, snap0.get("usage") if isinstance(snap0, dict) else None,
                                                   self.raw.get(p["sn"]))
            except Exception:  # noqa: BLE001 - dato facoltativo
                measured = None
        grams = self._record_filaments(job, progress if status != "finished" else 100, printer=p, measured=measured)
        snap = ""
        img = None
        if self.cfg.app.snapshot_on_finish and status in ("finished", "failed"):
            img = await self.get_snapshot(p["sn"], max_age=0)
            if img:
                snap = f"snapshots/job_{job['id']}.jpg"
                (self.cfg.data_path / snap).write_bytes(img)
        err = p["print_error"] if status == "failed" else ""
        self.db.update_job(job["id"], status=status, ended_at=time.time(), progress=progress,
                           filament_g=grams, snapshot=snap, error=err)
        self._open_jobs.pop(p["sn"], None)
        try:
            production.on_job_closed(self, job["id"], status, p["name"])
        except Exception:  # noqa: BLE001
            log.exception("Produzione: aggiornamento del pezzo non riuscito")
        name = job.get("task_name") or p["task_name"]
        dur = int((time.time() - (job.get("started_at") or time.time())) / 60)
        msg = JOB_TOASTS.get(status, JOB_TOASTS["unknown"])
        self.toast(msg, "error" if status == "failed" else "ok", sn=p["sn"], printer=p["name"], task=name)
        if (status == "finished" and self.cfg.notify.on_finish) or (status == "failed" and self.cfg.notify.on_failed):
            icon = "✅" if status == "finished" else "❌"
            title = self.nt(JOB_TITLES.get(status, JOB_TITLES["unknown"]), printer=p["name"])
            body = name + "\n" + self.nt("Durata {h} h {m} min, {g} g", h=dur // 60, m=dur % 60, g=grams)
            await self._notify(status, f"{icon} {title}", body, p["name"], img)

    def _record_filaments(self, job: dict, progress: float, printer: dict | None = None,
                          measured: list[dict] | None = None) -> float:
        """Registra il consumo per filamento: quello misurato dalla stampante se c'è (anche per le stampe fallite),
        altrimenti quello previsto, in proporzione all'avanzamento se la stampa non è finita."""
        factor = max(0.0, min(1.0, (progress or 0) / 100))
        try:
            plan = json.loads(job.get("filaments") or "[]")
        except ValueError:
            plan = []
        self.db.x("DELETE FROM job_filaments WHERE job_id=?", (job["id"],))
        total = 0.0
        if measured:
            # tipo e colore dal piano, se la misura non li conosce (es. bilance INTAMSYS)
            for i, f in enumerate(measured):
                ref = plan[i] if i < len(plan) else (plan[0] if len(plan) == 1 else {})
                g = round(float(f.get("grams") or 0), 1)
                if g <= 0:
                    continue
                total += g
                # "measured": misurato dalla stampante; "estimate": dato della stampante ma proporzionato (stampa interrotta)
                self.db.x("INSERT INTO job_filaments(job_id, type, brand, color, tray_info_idx, grams, meters, ams_id, slot_id, "
                          "source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (job["id"], f.get("type") if f.get("type") not in (None, "", "?") else ref.get("type", "?"),
                           f.get("brand") or ref.get("brand", ""), (f.get("color") or ref.get("color") or "").upper(), "",
                           g, f.get("meters") or 0, None, ref.get("slot_id"), "estimate" if f.get("estimated") else "measured"))
            return round(total, 1)
        for f in plan:
            g = round(f["grams"] * factor, 1)
            if g <= 0:
                continue
            total += g
            self.db.x("INSERT INTO job_filaments(job_id, type, brand, color, tray_info_idx, grams, meters, ams_id, slot_id) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (job["id"], f["type"], f.get("brand", ""), (f.get("color") or "").upper(),
                                                    f.get("tray_info_idx", ""), g, round(f.get("meters", 0) * factor, 2),
                                                    f.get("ams_id"), f.get("slot_id")))
        if not plan and printer:
            # stampa avviata fuori da FleetBuddy e file non in archivio: stima dal calo del residuo AMS
            for f in ams_usage(job.get("start_trays"), printer):
                total += f["grams"]
                self.db.x("INSERT INTO job_filaments(job_id, type, brand, color, tray_info_idx, grams, meters, ams_id, "
                          "slot_id, source) VALUES(?,?,?,?,?,?,0,?,?,'ams')",
                          (job["id"], f["type"], f["brand"], f["color"], f["idx"], f["grams"], f["ams_id"], f["slot_id"]))
        if not plan and not total:   # nessun dettaglio per filamento: solo il totale previsto, se noto
            total = (job.get("planned_g") or 0) * factor
        return round(total, 1)

    def backfill_filaments(self):
        """Una tantum: ricostruisce il consumo delle stampe già nello storico, quando il file è in archivio."""
        if self.db.get_setting("filament_backfill_v1"):
            return
        rows = self.db.q("""SELECT * FROM jobs WHERE status IN ('finished','failed','cancelled','unknown')
                            AND id NOT IN (SELECT DISTINCT job_id FROM job_filaments)""")
        fixed = 0
        for j in rows:
            f = self.db.file(j["file_id"]) if j.get("file_id") else None
            pl = None
            if f:
                pl = next((x for x in f["meta"].get("plates", []) if x["index"] == (j.get("plate") or 1)), None)
            if not pl:
                m = self._match_file(j.get("task_name") or "")
                if m:
                    f, pl = m
            if not (f and pl):
                continue
            plan = filament_plan(pl)
            j.update(file_id=f["id"], plate=pl["index"], filaments=json.dumps(plan))
            grams = self._record_filaments(j, 100 if j["status"] == "finished" else (j.get("progress") or 0))
            self.db.update_job(j["id"], file_id=f["id"], plate=pl["index"], filaments=j["filaments"],
                               planned_g=round(sum(x["grams"] for x in plan), 1), filament_g=grams)
            fixed += 1
        self.db.set_setting("filament_backfill_v1", True)
        if fixed:
            log.info("Ricostruito il consumo di filamento per %d stampe dello storico", fixed)

    async def _notify(self, event, title, message, printer="", image=None):
        await asyncio.to_thread(notifier.send, self.cfg.notify, event, title, message, printer, image)

    # ------------------------------------------------------------------ utilità
    async def describe(self, model: str, code: str, kind: str = "hms") -> str:
        key = f"{model}:{kind}:{code}"
        r = self.db.one("SELECT description FROM hms_cache WHERE key=?", (key,))
        if r:
            return r["description"]
        try:
            desc = await asyncio.to_thread(self.hub.hms_description, model, code, kind)
        except HubError:
            return ""
        if desc:
            self.db.x("INSERT OR REPLACE INTO hms_cache(key, description) VALUES(?,?)", (key, desc))
        return desc

    async def get_image(self, sn: str) -> tuple[bytes | None, str]:
        """Immagine per la scheda: la camera se c'è, altrimenti l'anteprima della stampa in corso
        (dalla stampante, se la fornisce, oppure dal file in archivio)."""
        img = await self.get_snapshot(sn, max_age=10)
        if img:
            return img, "camera"
        p = self.printers.get(sn) or {}
        key = f"{sn}:{p.get('task_id') or p.get('task_name')}"
        hit = self._thumb_cache.get(key)
        if hit and time.time() - hit[0] < 120:
            return hit[1], "thumbnail" if hit[1] else ""
        thumb = None
        c = self.direct.get(sn)
        if c and p.get("task_name"):
            try:
                thumb = await asyncio.to_thread(c.job_thumbnail, self.raw.get(sn))
            except Exception:  # noqa: BLE001
                thumb = None
        if not thumb:
            job = self._open_jobs.get(sn)
            fid = (job or {}).get("file_id")
            if not fid:
                pend = self._take_pending(sn, consume=False)
                fid = (pend or {}).get("file_id")
            f = self.db.file(fid) if fid else None
            if f:
                from .gcodemeta import thumbnail_png
                from .threemf import thumbnail
                path = self.cfg.path(f["path"])
                plate = (job or {}).get("plate") or 1
                thumb = await asyncio.to_thread(thumbnail_png if f["meta"].get("kind") == "gcode" else (lambda x: thumbnail(x, plate)), path)
        self._thumb_cache[key] = (time.time(), thumb)
        return thumb, "thumbnail" if thumb else ""

    async def get_snapshot(self, sn: str, max_age: float = 5) -> bytes | None:
        hit = self._snap_cache.get(sn)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        try:
            c = self.direct.get(sn)
            img = await asyncio.to_thread(c.snapshot if c else (lambda: self.hub.snapshot(sn)))
        except HubError:
            img = None
        if img:
            self._snap_cache[sn] = (time.time(), img)
        return img

    # ------------------------------------------------------------------ stampa
    def build_print_cmd(self, task_name: str, plate: int, slots: list[dict], options: dict,
                        printer: dict | None = None) -> dict:
        """Comando di stampa (API par. 4.6.2). Le opzioni cambiano con la famiglia della stampante:
        auto_bed_leveling / flow_dynamic_calibration solo per P1/A1/X1, i *_mode solo per H2/P2/X2:
        si inviano solo quelle della famiglia giusta, perché quelle dell'altra possono far rifiutare il comando."""
        lvl = bool(options.get("bed_leveling", True))
        flow = bool(options.get("flow_calibration", True))
        model = ((printer or {}).get("model") or "").upper()
        opt = {"timelapse": bool(options.get("timelapse", False))}
        if model.startswith(("H2", "P2", "X2")):
            opt.update(bed_leveling_mode=2 if lvl else 0, flow_dynamic_cali_mode=2 if flow else 0)
            if (printer or {}).get("dual_nozzle"):
                opt["nozzle_offset_cali_mode"] = 2
        else:
            opt.update(auto_bed_leveling=lvl, flow_dynamic_calibration=flow)
        return {
            "task_name": task_name.encode("utf-8")[:120].decode("utf-8", "ignore"),
            "plate": f"Metadata/plate_{plate}.gcode",
            "print_option": opt,
            "filament_slot": slots,
        }

    async def start_remote(self, sn: str, path: str, user_id: int | None):
        """Avvia un file già presente nella memoria della stampante (es. Raise3D, che non accetta caricamenti)."""
        c = self.direct[sn]
        task = task_name_for(Path(path).name, 1, 1)
        self._set_pending(sn, {"file_id": None, "plate": 1, "task_name": task, "user_id": user_id,
                               "planned_g": 0, "plan": [], "queue": None})
        try:
            await asyncio.to_thread(c.start_remote, path)
        except Exception:
            self.db.x("DELETE FROM pending_prints WHERE printer_sn=?", (sn,))
            raise
        self._cooldown[sn] = time.time() + 20

    async def _start_direct(self, f: dict, pl: dict, sn: str, user_id: int | None, queue: dict | None,
                            options: dict | None = None) -> dict:
        """Carica il .gcode sulla stampante diretta e lo avvia."""
        task = task_name_for(f["filename"], 1, 1)
        plan = [{"type": x["type"], "brand": "", "color": x["color"], "tray_info_idx": "", "grams": x["used_g"],
                 "meters": x["used_m"], "ams_id": None, "slot_id": x.get("tool")} for x in pl["filaments"] if x["used_g"] or x["used_m"]]
        self._set_pending(sn, {"file_id": f["id"], "plate": 1, "task_name": task, "user_id": user_id,
                               "planned_g": pl["weight_g"], "plan": plan, "queue": queue})
        try:
            await asyncio.to_thread(self.direct[sn].start, str(self.cfg.path(f["path"])), Path(f["filename"]).name, options or {})
        except Exception:
            self.db.x("DELETE FROM pending_prints WHERE printer_sn=?", (sn,))
            raise
        self._cooldown[sn] = time.time() + 20
        log.info("Stampa inviata a %s (%s): %s", sn, self.direct[sn].label, f["filename"])
        return {"ok": True, "cached": False, "warnings": [], "print_cmd": {"file": f["filename"]}, "hub": {}}

    async def start_print(self, file_id: int, plate: int, sn: str, slots: list[dict] | None,
                          options: dict | None = None, user_id: int | None = None, queue: dict | None = None) -> dict:
        f = self.db.file(file_id)
        if not f:
            raise HubError(key="File non trovato nell'archivio")
        pl = next((x for x in f["meta"]["plates"] if x["index"] == plate), None)
        if not pl:
            raise HubError(key="Piatto {n} non presente nel file", params={"n": plate})
        p = self.printers.get(sn)
        if not p:
            raise HubError(key="Stampante sconosciuta o offline")
        gcode_file = f["meta"].get("kind") == "gcode"
        if gcode_file != (sn in self.direct):
            raise HubError(key="Questo file non è adatto a questa stampante: i .gcode.3mf vanno alle Bambu, i .gcode alle altre")
        if gcode_file:
            if not self.direct[sn].caps.get("upload", True):
                raise HubError(key="Questa stampante non accetta file dalla rete: avvia un file presente nella sua memoria")
            if f["meta"].get("format") == "ufp" and not self.direct[sn].caps.get("ufp"):
                raise HubError(key="I file .ufp si possono stampare solo sulle UltiMaker")
            return await self._start_direct(f, pl, sn, user_id, queue, options)
        warnings: list[str] = []
        slots, warnings = resolve_slots(slots, pl["filaments"], p)
        if slots is None:
            raise HubError(key="Mappatura automatica impossibile: {w}", params={"w": "; ".join(warnings)})
        task = task_name_for(f["filename"], plate, len(f["meta"]["plates"]))
        cmd = self.build_print_cmd(task, plate, slots, options or {}, p)
        plan = filament_plan(pl, slots, p)
        # registrata prima dell'invio: la stampante può iniziare prima che il Hub risponda
        self._set_pending(sn, {"file_id": file_id, "plate": plate, "task_name": task, "user_id": user_id,
                               "planned_g": round(sum(x["grams"] for x in plan), 1) or pl["weight_g"], "plan": plan,
                               "queue": queue})

        cached = False
        try:
            hub_files = await asyncio.to_thread(self.hub.cached_files, 500)
            cached = any(x.get("file_hash") == f["md5"] for x in hub_files)
        except HubError:
            pass
        try:
            try:
                if cached:
                    res = await asyncio.to_thread(self.hub.print_cached, f["md5"], cmd, [sn])
                else:
                    raise HubError("not cached", code=-1)
            except HubError as e:
                if e.code != -1 and e.code in (1051, 1052, 1053):
                    raise
                res = await asyncio.to_thread(self.hub.print_upload, str(self.cfg.path(f["path"])),
                                              f["filename"], cmd, [sn])
        except Exception:
            self.db.x("DELETE FROM pending_prints WHERE printer_sn=?", (sn,))
            raise
        self._cooldown[sn] = time.time() + 45
        log.info("Stampa inviata a %s (%s, file %s): print_cmd=%s risposta Hub=%s", sn, p["model"],
                 "dalla cache" if cached else "caricato", json.dumps(cmd, ensure_ascii=False), res)
        return {"ok": True, "cached": cached, "warnings": warnings, "print_cmd": cmd, "hub": res}

    # ------------------------------------------------------------------ coda
    async def _dispatch_queue(self):
        if self.db.get_setting("queue_paused", False):
            return
        items = self.db.q("SELECT * FROM queue WHERE status='queued' ORDER BY position, id")
        busy: set[str] = set()
        for it in items:
            f = self.db.file(it["file_id"])
            if not f:
                self.db.x("UPDATE queue SET status='error', message=? WHERE id=?", (qmsg("file rimosso"), it["id"]))
                continue
            pl = next((x for x in f["meta"]["plates"] if x["index"] == it["plate"]), None)
            if it["printer_sn"]:
                targets = [it["printer_sn"]]
            elif it.get("group_id"):
                targets = sorted(r["printer_sn"] for r in self.db.q(
                    "SELECT printer_sn FROM group_printers WHERE group_id=?", (it["group_id"],)))
            else:
                targets = sorted(self.printers)
            for sn in targets:
                p = self.printers.get(sn)
                if not p or sn in busy or not is_ready(p) or self._cooldown.get(sn, 0) > time.time() \
                        or self.has_pending(sn):
                    continue
                if (f["meta"].get("kind") == "gcode") != (sn in self.direct):
                    continue   # .gcode.3mf solo alle Bambu, .gcode solo alle stampanti dirette
                if sn in self.direct and not self.direct[sn].caps.get("upload", True):
                    continue   # stampante che non accetta file dalla rete (es. Raise3D)
                if f["meta"].get("format") == "ufp" and not (sn in self.direct and self.direct[sn].caps.get("ufp")):
                    continue   # i .ufp solo alle UltiMaker
                raw = json.loads(it["mapping"]) if it["mapping"] else None
                slots, _w = ([], []) if sn in self.direct else resolve_slots(raw, pl["filaments"] if pl else [], p)
                if slots is None:
                    # filamento non ancora caricato: la stampa resta in coda finché non lo è
                    if it["printer_sn"]:
                        note = qmsg("in attesa del filamento su {printer}", printer=p["name"])
                        if note != it.get("message"):
                            self.db.x("UPDATE queue SET message=? WHERE id=?", (note, it["id"]))
                    continue   # questa stampante non ha i filamenti: prova la prossima
                busy.add(sn)
                try:
                    left = (it["copies"] or 1) - 1
                    await self.start_print(it["file_id"], it["plate"], sn, slots, json.loads(it["options"] or "{}"),
                                           user_id=it.get("created_by"),
                                           queue={"id": it["id"], "copy": left > 0})
                    if left > 0:
                        self.db.x("UPDATE queue SET copies=?, message=? WHERE id=?",
                                  (left, qmsg("ultima copia inviata a {printer}", printer=p["name"]), it["id"]))
                    else:
                        # resta in coda come "in attesa di partire" finché la stampante non inizia davvero
                        self.db.x("UPDATE queue SET status='sent', started_at=?, printer_sn=?, message=? "
                                  "WHERE id=?", (time.time(), sn, qmsg("inviata a {printer}, in attesa che parta",
                                                                       printer=p["name"]), it["id"]))
                    self.toast("Coda: {file} inviata a {printer}", "ok", sn=sn, file=f["filename"], printer=p["name"])
                    if self.cfg.notify.on_queue_start:
                        await self._notify("queue", f"▶️ {p['name']}", self.nt("Avviata dalla coda: {file}", file=f["filename"]),
                                           p["name"])
                except HubError as e:
                    self._cooldown[sn] = time.time() + 120
                    if e.code not in (1051,):
                        self.db.x("UPDATE queue SET status='error', message=? WHERE id=?",
                                  (qmsg(e.key, **{k: str(v) for k, v in e.params.items()}) if e.key else str(e), it["id"]))
                    self.toast("Coda: errore su {printer}: {error}", "error", sn=sn, printer=p["name"],
                               error=e.localized(self.notify_lang))
                except Exception as e:  # noqa: BLE001 - un errore imprevisto blocca solo questa voce, non la coda
                    log.exception("Coda: errore imprevisto inviando la voce %s a %s", it["id"], sn)
                    self._cooldown[sn] = time.time() + 120
                    self.db.x("UPDATE queue SET status='error', message=? WHERE id=?", (str(e)[:300], it["id"]))
                break
