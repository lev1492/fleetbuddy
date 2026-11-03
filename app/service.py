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
from .hub_client import FleetHubClient, HubError
from .i18n import tr


def qmsg(msg: str, **params) -> str:
    """Messaggio di coda salvato come frase + parametri, tradotto dal browser."""
    return json.dumps({"msg": msg, "params": params}, ensure_ascii=False)
from .printers import auto_map, is_ready, normalize

log = logging.getLogger("fleetbuddy.service")

# avvisi di fine stampa (frasi del catalogo traduzioni)
JOB_TOASTS = {"finished": "{printer}: stampa completata – {task}", "failed": "{printer}: stampa fallita – {task}",
              "cancelled": "{printer}: stampa annullata – {task}", "unknown": "{printer}: stampa interrotta – {task}"}
JOB_TITLES = {"finished": "{printer}: stampa completata", "failed": "{printer}: stampa fallita",
              "cancelled": "{printer}: stampa annullata", "unknown": "{printer}: stampa interrotta"}


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = DB(cfg.data_path / "fleetbuddy.db")
        self.hub = FleetHubClient(cfg)
        self.printers: dict[str, dict] = {}
        self.hub_ok = False
        self.hub_error = ""
        self.hub_error_obj: HubError | None = None
        self.last_poll = 0.0
        self._subscribers: set[asyncio.Queue] = set()
        self._open_jobs = self.db.open_jobs()
        self._pending: dict[str, dict] = {}     # sn -> stampa appena inviata da FleetBuddy
        self._cooldown: dict[str, float] = {}   # sn -> non inviare altro fino a ...
        self.stopped_by_user: set[str] = set()   # interruzioni chieste da qui -> 'annullata', non 'fallita'
        self._hms_seen: dict[str, set] = {}
        self._snap_cache: dict[str, tuple[float, bytes]] = {}
        self._task: asyncio.Task | None = None
        for sub in ("archive", "snapshots", "tmp"):
            (cfg.data_path / sub).mkdir(exist_ok=True)

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
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while True:
            t0 = time.time()
            try:
                raw = await asyncio.to_thread(self.hub.devices)
                new = {p["sn"]: p for p in (normalize(d) for d in raw) if p["sn"]}
                self.hub_ok, self.hub_error, self.hub_error_obj = True, "", None
                for sn, p in new.items():
                    await self._track(p, self.printers.get(sn))
                self.printers = new
                self.last_poll = time.time()
                await self._dispatch_queue()
            except HubError as e:
                if str(e) != self.hub_error:   # registra solo i cambi di errore, non ogni polling
                    log.warning("Polling fallito: %s", e)
                self.hub_ok, self.hub_error, self.hub_error_obj = False, str(e), e
            except Exception as e:  # noqa: BLE001 - il ciclo non deve mai morire
                self.hub_ok, self.hub_error = False, f"Errore interno: {e}"
                self.hub_error_obj = HubError(key="Errore interno: {e}", params={"e": e})
                log.exception("Errore nel ciclo di polling")
            self.emit("state", self.snapshot_state())
            await asyncio.sleep(max(1.0, self.cfg.app.poll_interval - (time.time() - t0)))

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
                pend = self._pending.pop(sn, None)
                if pend and time.time() - pend["at"] > 1800:
                    pend = None
                jid = self.db.start_job(
                    printer_sn=sn, printer_name=p["name"], task_id=key,
                    task_name=(pend or {}).get("task_name") or p["task_name"],
                    file_id=(pend or {}).get("file_id"), plate=(pend or {}).get("plate"),
                    planned_g=(pend or {}).get("planned_g", 0), started_by=(pend or {}).get("user_id"))
                self._open_jobs[sn] = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
                self.toast("{printer}: stampa avviata – {task}", sn=sn, printer=p["name"], task=p["task_name"])
            else:
                self.db.update_job(job["id"], progress=p["progress"])
        elif job:
            if p["state"] == "FINISH":
                await self._close_job(job, p, "finished")
            elif p["state"] == "FAILED":
                await self._close_job(job, p, "failed")
            elif p["state"] == "IDLE":
                await self._close_job(job, p, "finished" if job.get("progress", 0) >= 99 else "cancelled")
            elif not p["online"] and prev and prev["online"]:
                pass  # resta aperto: potrebbe tornare online e continuare

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
        grams = job.get("planned_g") or 0
        grams = grams if status == "finished" else round(grams * progress / 100, 1)
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
        name = job.get("task_name") or p["task_name"]
        dur = int((time.time() - (job.get("started_at") or time.time())) / 60)
        msg = JOB_TOASTS.get(status, JOB_TOASTS["unknown"])
        self.toast(msg, "error" if status == "failed" else "ok", sn=p["sn"], printer=p["name"], task=name)
        if (status == "finished" and self.cfg.notify.on_finish) or (status == "failed" and self.cfg.notify.on_failed):
            icon = "✅" if status == "finished" else "❌"
            title = self.nt(JOB_TITLES.get(status, JOB_TITLES["unknown"]), printer=p["name"])
            body = name + "\n" + self.nt("Durata {h} h {m} min, {g} g", h=dur // 60, m=dur % 60, g=grams)
            await self._notify(status, f"{icon} {title}", body, p["name"], img)

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

    async def get_snapshot(self, sn: str, max_age: float = 5) -> bytes | None:
        hit = self._snap_cache.get(sn)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        try:
            img = await asyncio.to_thread(self.hub.snapshot, sn)
        except HubError:
            img = None
        if img:
            self._snap_cache[sn] = (time.time(), img)
        return img

    # ------------------------------------------------------------------ stampa
    def build_print_cmd(self, task_name: str, plate: int, slots: list[dict], options: dict) -> dict:
        lvl = bool(options.get("bed_leveling", True))
        flow = bool(options.get("flow_calibration", True))
        return {
            "task_name": task_name.encode("utf-8")[:120].decode("utf-8", "ignore"),
            "plate": f"Metadata/plate_{plate}.gcode",
            "print_option": {
                "auto_bed_leveling": lvl,
                "flow_dynamic_calibration": flow,
                "timelapse": bool(options.get("timelapse", False)),
                "bed_leveling_mode": 2 if lvl else 0,
                "flow_dynamic_cali_mode": 2 if flow else 0,
                "nozzle_offset_cali_mode": 2,
            },
            "filament_slot": slots,
        }

    async def start_print(self, file_id: int, plate: int, sn: str, slots: list[dict] | None,
                          options: dict | None = None, user_id: int | None = None) -> dict:
        f = self.db.file(file_id)
        if not f:
            raise HubError(key="File non trovato nell'archivio")
        pl = next((x for x in f["meta"]["plates"] if x["index"] == plate), None)
        if not pl:
            raise HubError(key="Piatto {n} non presente nel file", params={"n": plate})
        p = self.printers.get(sn)
        if not p:
            raise HubError(key="Stampante sconosciuta o offline")
        warnings: list[str] = []
        if not slots:
            slots, warnings = auto_map(pl["filaments"], p)
            if slots is None:
                raise HubError(key="Mappatura automatica impossibile: {w}", params={"w": "; ".join(warnings)})
        task = Path(f["filename"]).name.replace(".gcode.3mf", "").replace(".3mf", "")
        if len(f["meta"]["plates"]) > 1:
            task += f"_plate_{plate}"
        cmd = self.build_print_cmd(task, plate, slots, options or {})

        cached = False
        try:
            hub_files = await asyncio.to_thread(self.hub.cached_files, 500)
            cached = any(x.get("file_hash") == f["md5"] for x in hub_files)
        except HubError:
            pass
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
        self._pending[sn] = {"at": time.time(), "file_id": file_id, "plate": plate,
                             "task_name": task, "planned_g": pl["weight_g"], "user_id": user_id}
        self._cooldown[sn] = time.time() + 45
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
                if not p or sn in busy or not is_ready(p) or self._cooldown.get(sn, 0) > time.time():
                    continue
                slots = json.loads(it["mapping"]) if it["mapping"] else None
                if slots is None:
                    slots, _ = auto_map(pl["filaments"] if pl else [], p)
                    if slots is None:
                        continue   # questa stampante non ha i filamenti: prova la prossima
                busy.add(sn)
                try:
                    await self.start_print(it["file_id"], it["plate"], sn, slots, json.loads(it["options"] or "{}"),
                                           user_id=it.get("created_by"))
                    left = (it["copies"] or 1) - 1
                    if left > 0:
                        self.db.x("UPDATE queue SET copies=?, message=? WHERE id=?",
                                  (left, qmsg("ultima copia inviata a {printer}", printer=p["name"]), it["id"]))
                    else:
                        self.db.x("UPDATE queue SET status='started', started_at=?, printer_sn=?, message=? "
                                  "WHERE id=?", (time.time(), sn, qmsg("inviata a {printer}", printer=p["name"]), it["id"]))
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
                break
