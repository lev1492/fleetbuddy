"""Stampanti UltiMaker: unione della API della stampante e di UltiMaker Connect.

Base (tutti i modelli: UM3, S3, S5, S7, S6, S8, Factor 4), API /api/v1 della stampante:
  stato       GET /api/v1/printer (stato, piano, testine, ugelli), GET /api/v1/print_job (404 = nessun lavoro),
              GET /api/v1/system/name|firmware|variant
  comandi     PUT /api/v1/print_job/state {"target": "pause"|"print"|"abort"}
              PUT /api/v1/printer/bed/temperature, PUT /api/v1/printer/heads/0/extruders/{n}/hotend/temperature
  stampa      POST /api/v1/print_job (multipart: jobname, file .gcode / .gcode.gz / .ufp)
  camera      GET /api/v1/camera/0/snapshot
Integrazione UltiMaker Connect (se risponde; sulla stampante "host" del gruppo):
  /cluster-api/v1/printers/          materiali caricati (marca, tipo, colore), Material Station, manutenzione, guasti
  /cluster-api/v1/print_jobs/printing  lavoro in corso → anteprima (/print_jobs/{uuid}/preview_image)
  /material-station-api/v1/material-station/   umidità e bobine della Material Station
  /fault-api/v1/faults/              errori e avvisi della stampante
Autorizzazione: i comandi richiedono Digest (SHA-256). Si ottiene con POST /api/v1/auth/request e
l'approvazione sul display della stampante; FleetBuddy la richiede dal modulo in Amministrazione.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from requests.auth import HTTPDigestAuth

from ..hub_client import HubError
from .base import DirectClient, base_state, bed, mm_to_grams, nozzle

JOB_STATE = {"printing": "RUNNING", "pre_print": "PREPARE", "pausing": "PAUSE", "paused": "PAUSE", "resuming": "RUNNING",
             "post_print": "RUNNING", "wait_cleanup": "FINISH", "wait_user_action": "PAUSE", "sent_to_printer": "PREPARE"}
PRINTER_STATE = {"idle": "IDLE", "printing": "RUNNING", "error": "FAILED", "maintenance": "PREPARE", "booting": "PREPARE",
                 "pre_print": "PREPARE", "unreachable": "OFFLINE"}
RESULT = {"finished": "finished", "aborted": "cancelled", "failed": "failed"}
def N_(s: str) -> str:
    """Segna una frase da tradurre (la traduzione avviene nell'interfaccia)."""
    return s


# codici di errore di UltiMaker Connect (PrinterError) → frasi del catalogo traduzioni
ERRORS = {"air_manager_fan_speed_unexpected": N_("Air Manager: velocità della ventola anomala"),
          "air_manager_filter_missing": N_("Filtro dell'Air Manager mancante"),
          "air_manager_filter_needs_replacement": N_("Filtro dell'Air Manager da sostituire"),
          "air_manager_missing": N_("Air Manager non collegato"),
          "air_manager_not_configured": N_("Air Manager non configurato"),
          "build_chamber_temp_critical": N_("Temperatura della camera di stampa critica"),
          "build_chamber_too_hot": N_("Camera di stampa troppo calda")}
COLORS = {"black": "#1A1A1A", "white": "#F5F5F5", "red": "#D32F2F", "blue": "#1E5BD8", "green": "#2E9E44", "yellow": "#F2C230",
          "orange": "#F07C1E", "grey": "#8C8C8C", "gray": "#8C8C8C", "silver": "#BDBDBD", "natural": "#EDE6D3",
          "transparent": "#E8F1F2", "purple": "#7B3FA0", "pink": "#E85A9B", "brown": "#7A4A2A", "magenta": "#C2187A"}


def _color(name: str | None) -> str:
    if not name:
        return "#888888"
    if name.startswith("#"):
        return name[:7].upper()
    low = name.lower()
    return next((v for k, v in COLORS.items() if k in low), "#888888")


class UltimakerClient(DirectClient):
    kind = "ultimaker"
    label = "UltiMaker"
    default_brand = "UltiMaker"
    caps = {"temperature": True, "speed": False, "filament": False, "bed_clean": False, "print_options": [],
            "upload": True, "ufp": True, "chamber_heater": False}

    late_usage = True      # lo storico della stampante può essere scritto dopo la fine: si riprova più tardi

    def __init__(self, row: dict):
        super().__init__(row)
        self._materials: dict[str, dict] = {}
        if self.username and self.password:
            self._s.auth = HTTPDigestAuth(self.username, self.password)
        self.system: dict = {}
        self.ip = urlsplit(self.base).hostname
        self._cache: dict[str, tuple[float, object]] = {}
        self._connect_ok: bool | None = None       # None = ancora da provare
        self._connect_retry = 0.0
        self._thumb: tuple[str, bytes | None] = ("", None)

    # ------------------------------------------------------------------ supporto
    def _get(self, path: str, allow_404=True):
        r = self.request("GET", path, ok=(200, 404) if allow_404 else (200,))
        if r.status_code == 404:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text.strip().strip('"')

    def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            val = fn()
        except HubError:
            val = None
        self._cache[key] = (time.time(), val)
        return val

    def probe(self) -> dict:
        """Codici di risposta degli endpoint principali e modalità di accesso: per capire le differenze tra modelli."""
        out = {}
        for path in ("/api/v1/printer", "/api/v1/system/name", "/cluster-api/v1/printers/",
                     "/cluster-api/v1/system/authentication_mode/"):
            try:
                r = self._s.get(self.base + path, timeout=5)
                out[path] = r.status_code
                if path.endswith("authentication_mode/") and r.status_code == 200:
                    out["mode"] = (r.json() or {}).get("mode")
            except Exception:  # noqa: BLE001
                out[path] = "—"
        return out

    def _probe_text(self) -> str:
        p = self.probe()
        parts = [f"{k.rstrip('/').rsplit('/', 2)[-2] + '/' + k.rstrip('/').rsplit('/', 1)[-1]}: {v}" for k, v in p.items() if k != "mode"]
        return ", ".join(parts) + (f", modalità di accesso: {p['mode']}" if p.get("mode") else "")

    @staticmethod
    def _denied(e: HubError) -> bool:
        return (e.key or "").startswith("Accesso negato")

    def _auth_error(self, e: HubError):
        if not self._s.auth:
            raise HubError(key="La stampante UltiMaker richiede l'autorizzazione: usa \"Richiedi autorizzazione\" in Amministrazione") from e
        raise e

    # ------------------------------------------------------------------ lettura
    def model(self) -> dict:
        if not self.system:
            sysd = {}
            for k in ("name", "firmware", "variant"):        # sui modelli recenti possono richiedere l'autorizzazione
                try:
                    sysd[k] = self._get(f"/api/v1/system/{k}")
                except HubError:
                    sysd[k] = None
            self.system = sysd
        try:
            printer = self._get("/api/v1/printer", allow_404=False)
            job = self._get("/api/v1/print_job")
        except HubError as e:
            if not self._denied(e):
                raise
            if not self._s.auth:
                raise HubError(key="La stampante UltiMaker chiede l'autorizzazione anche per leggere lo stato: usa \"Richiedi autorizzazione\" ({d})",
                               params={"d": self._probe_text()}) from e
            raise HubError(key="La stampante UltiMaker ha rifiutato l'autorizzazione salvata: richiedine una nuova ({d})",
                           params={"d": self._probe_text()}) from e
        raw = {"system": self.system, "printer": printer, "job": job}
        # UltiMaker Connect: facoltativo (risponde sulla stampante host del gruppo); si riprova ogni 5 minuti se manca
        if self._connect_ok is not False or time.time() > self._connect_retry:
            printers = self._cached("cluster_printers", 10, lambda: self._get("/cluster-api/v1/printers/"))
            self._connect_ok = isinstance(printers, list)
            if self._connect_ok:
                mine = [p for p in printers if p.get("ip_address") == self.ip] or (printers if len(printers) == 1 else [])
                raw["cluster_printer"] = mine[0] if mine else None
                jobs = self._cached("cluster_jobs", 10, lambda: self._get("/cluster-api/v1/print_jobs/printing")) or []
                uid = (raw["cluster_printer"] or {}).get("uuid")
                raw["cluster_job"] = next((j for j in jobs if uid and uid in (j.get("printer_uuid"), j.get("assigned_to"))), None)
            else:
                self._connect_retry = time.time() + 300
        raw["faults"] = self._cached("faults", 30, lambda: self._get("/fault-api/v1/faults/"))
        raw["material_station"] = self._cached("misp", 30, lambda: self._get("/material-station-api/v1/material-station/"))
        return raw

    def normalize(self, raw: dict | None, error: str = "") -> dict:
        if not raw:
            return base_state(self, False, error, model="UltiMaker")
        pr, job, sysd = raw.get("printer") or {}, raw.get("job") or {}, raw.get("system") or {}
        cp = raw.get("cluster_printer") or {}
        jstate = (job.get("state") or "none").lower()
        if job and jstate in JOB_STATE:
            state = JOB_STATE[jstate]
        else:
            state = PRINTER_STATE.get((pr.get("status") or "idle").lower(), "IDLE")
        name = job.get("name") or ""
        result = RESULT.get((job.get("result") or "").lower())
        conf = {c.get("extruder_index"): c for c in cp.get("configuration") or []}
        noz = []
        for i, ex in enumerate(((pr.get("heads") or [{}])[0] or {}).get("extruders") or []):
            hot = ex.get("hotend") or {}
            temp = hot.get("temperature") or {}
            core = hot.get("id") or (conf.get(i) or {}).get("print_core_id") or ""
            m = re.search(r"(\d+\.\d+)", core)
            mat = (conf.get(i) or {}).get("material") or {}
            two = len(((pr.get("heads") or [{}])[0] or {}).get("extruders") or []) > 1
            noz.append(nozzle(i, "" if two else f"{i + 1}", temp.get("current"), temp.get("target"), type_name=core or None,
                              side=("left" if i == 0 else "right") if two else "tool",
                              diameter=float(m.group(1)) if m else None,
                              material_label=" ".join(x for x in (mat.get("brand"), mat.get("material"), mat.get("color")) if x) or None,
                              material_color=_color(mat.get("color")) if mat else None))
        bt = ((pr.get("bed") or {}).get("temperature") or {})
        # Material Station (S5/S7 Pro Bundle): bobine come slot, con umidità e residuo
        ms_c = cp.get("material_station") or {}
        ms_s = raw.get("material_station") or {}
        slots = ms_c.get("material_slots") or []
        trays, units = [], []
        if slots or ms_s.get("slots"):
            direction = {x.get("slot_index"): x.get("direction") for x in ms_s.get("slots") or []}
            for s_ in (slots or ms_s.get("slots") or []):
                mat = s_.get("material") or {}
                rem = s_.get("material_remaining")
                loaded = bool(mat) or bool(s_.get("material_guid"))
                idx = s_.get("slot_index", len(trays))
                ext = s_.get("extruder_index", direction.get(idx))   # 0 = estrusore sinistro, 1 = destro
                trays.append({"ams_id": 0, "slot_id": idx, "label": str(idx + 1),
                              "kind": "ams", "loaded": loaded, "type": mat.get("material") or ("?" if loaded else ""),
                              "color": _color(mat.get("color")) if loaded else "", "sub_brand": mat.get("brand") or "",
                              "remain": round(rem * 100) if isinstance(rem, (int, float)) and rem >= 0 else None,
                              "remain_g": None, "rfid": (s_.get("material_selection_source") or "") == "RFID", "idx": "",
                              "nozzle": ext if isinstance(ext, int) and ext >= 0 else None, "core": s_.get("print_core_id") or "",
                              "empty_alert": bool(s_.get("material_empty"))})
            units.append({"ams_id": 0, "kind": "Material Station", "letter": "", "nozzle": None, "temp": None, "row": True,
                          "humidity": round(ms_s["current_humidity"]) if isinstance(ms_s.get("current_humidity"), (int, float)) else None})
        alerts = []
        seen = set()
        for e in cp.get("errors") or []:                           # errori noti di Connect: tradotti
            code = e.get("code") if isinstance(e, dict) else None
            if code and code not in seen:
                seen.add(code)
                alerts.append({"msg": ERRORS.get(code, N_("Errore della stampante: {c}")), "params": {"c": code}})
        for f in (raw.get("faults") or []) + (cp.get("faults") or []):
            if isinstance(f, dict) and (f.get("message") or f.get("code")) and f.get("code") not in seen:
                seen.add(f.get("code"))
                sev = (f.get("severity") or "").upper()
                alerts.append({"text": f"{'⚠ ' if sev in ('ERROR', 'CRITICAL') else ''}{f.get('code', '')}: {f.get('message', '')}".strip(": ")})
        if cp.get("maintenance_required"):
            alerts.append({"msg": "La stampante richiede manutenzione"})
        if cp.get("firmware_update_status") == "update_available":
            alerts.append({"msg": "Aggiornamento del firmware disponibile: {v}", "params": {"v": cp.get("latest_available_firmware") or ""}})
        if ms_s.get("door_opened"):
            alerts.append({"msg": "Sportello della Material Station aperto"})
        if (ms_s.get("dehumidifier_status") or "") == "error":
            alerts.append({"msg": "Errore del deumidificatore della Material Station"})
        for t_ in trays:
            if t_.get("empty_alert"):
                alerts.append({"msg": "Bobina {n} della Material Station esaurita", "params": {"n": t_["label"]}})
        # Air Manager (S5/S7/Factor 4): stato e usura del filtro
        am = cp.get("air_manager") or {}
        air = None
        if am.get("supported") and am.get("status") not in (None, "unavailable"):
            age, mx = am.get("filter_age"), am.get("filter_max_age")
            air = {"status": am.get("status"), "filter_status": am.get("filter_status"),
                   "filter_pct": round(100 * age / mx) if isinstance(age, (int, float)) and mx else None,
                   "filter_hours_left": max(0, mx - age) if isinstance(age, (int, float)) and mx else None}
            fs = am.get("filter_status")
            if fs in ("replacement_near", "replacement_required", "missing") and "air_manager_filter_needs_replacement" not in seen:
                alerts.append({"msg": {"replacement_near": N_("Filtro dell'Air Manager quasi da sostituire"),
                                       "replacement_required": N_("Filtro dell'Air Manager da sostituire"),
                                       "missing": N_("Filtro dell'Air Manager mancante")}[fs]})
        if jstate == "wait_user_action":
            alerts.append({"msg": "La stampante richiede un intervento sul display"})
        thumb_job = (raw.get("cluster_job") or {}).get("uuid") or ""
        return base_state(
            self, True, error, state=state, task_name=name if (state in ("RUNNING", "PAUSE", "PREPARE", "FINISH") or result) else "",
            task_id=job.get("uuid") or name, progress=int(round((job.get("progress") or 0) * 100)),
            remaining_min=int(max(0, (job.get("time_total") or 0) - (job.get("time_elapsed") or 0)) // 60),
            nozzles=noz, beds=[bed(0, "", bt.get("current"), bt.get("target"))],
            trays=trays, ams_units=units, firmware=str(sysd.get("firmware") or cp.get("firmware_version") or ""),
            model=str(sysd.get("variant") or cp.get("machine_variant") or "UltiMaker"),
            alerts=alerts + ([{"msg": "Rimuovi la stampa e conferma sul display della stampante"}] if jstate == "wait_cleanup" else []),
            air_manager=air, dual_nozzle=len(noz) > 1, last_result=result if name else None,
            caps={**self.caps, "connect": bool(self._connect_ok), "authorized": bool(self._s.auth), "thumb_job": thumb_job})

    def info(self, raw: dict) -> dict:
        s = raw.get("system") or {}
        mode = self.probe().get("mode")
        return {"board": str(s.get("variant") or "UltiMaker"), "firmware": str(s.get("firmware") or ""),
                "mode": ("Connect" if self._connect_ok else "API /api/v1") + (f" · accesso {mode}" if mode else ""),
                "status": (raw.get("printer") or {}).get("status"),
                "tools": len((((raw.get("printer") or {}).get("heads") or [{}])[0] or {}).get("extruders") or [])}

    def job_thumbnail(self, raw: dict | None) -> bytes | None:
        uid = ((raw or {}).get("cluster_job") or {}).get("uuid")
        if not uid:
            return None
        if self._thumb[0] == uid:
            return self._thumb[1]
        try:
            r = self.request("GET", f"/cluster-api/v1/print_jobs/{uid}/preview_image")
            img = r.content if r.content[:8] == b"\x89PNG\r\n\x1a\n" or r.content[:2] == b"\xff\xd8" else None
        except HubError:
            img = None
        self._thumb = (uid, img)
        return img

    def snapshot(self) -> bytes | None:
        if self.snapshot_url:
            return super().snapshot()
        try:
            r = self._s.get(self.base + "/api/v1/camera/0/snapshot", timeout=6, allow_redirects=True)
        except Exception:  # noqa: BLE001
            return None
        return r.content if r.status_code == 200 and r.content[:2] == b"\xff\xd8" else None

    def has_snapshot(self) -> bool:
        return True

    # ------------------------------------------------------------------ comandi
    def _state(self, target: str):
        try:
            self.request("PUT", "/api/v1/print_job/state", json={"target": target})
        except HubError as e:
            self._auth_error(e)

    def pause(self):
        self._state("pause")

    def resume(self):
        self._state("print")

    def stop(self):
        self._state("abort")

    def set_temperature(self, target: str, value: float):
        v = max(0.0, min(350.0, float(value)))
        try:
            if target == "bed":
                self.request("PUT", "/api/v1/printer/bed/temperature", data={"temperature": v})
            elif target.startswith("tool:"):
                self.request("PUT", f"/api/v1/printer/heads/0/extruders/{int(target[5:])}/hotend/temperature", data={"temperature": v})
            else:
                raise HubError(key="Riscaldatore non valido")
        except HubError as e:
            self._auth_error(e)

    def start(self, local_path: str, name: str, options: dict | None = None):
        jobname = re.sub(r"\.(gcode\.gz|gcode|ufp)$", "", name, flags=re.I)
        try:
            with open(local_path, "rb") as fh:
                self.request("POST", "/api/v1/print_job", data={"jobname": jobname, "owner": "FleetBuddy"},
                             files={"file": (name, fh, "application/octet-stream")}, timeout=600)
        except HubError as e:
            self._auth_error(e)

    # ------------------------------------------------------------------ consumo dallo storico della stampante
    def material(self, guid: str) -> dict:
        """Profilo del materiale (XML "fdmmaterial") da /api/v1/materials/{guid}: marca, tipo, colore, densità, diametro."""
        if not guid:
            return {}
        if guid in self._materials:
            return self._materials[guid]
        info: dict = {}
        try:
            xml = self._get(f"/api/v1/materials/{guid}")
            info = parse_material(xml if isinstance(xml, str) else "")
        except HubError:
            info = {}
        self._materials[guid] = info
        return info

    def usage_snapshot(self, raw: dict | None) -> dict | None:
        uid = ((raw or {}).get("job") or {}).get("uuid")
        return {"uuid": uid} if uid else None

    def usage_since(self, snapshot: dict | None, raw: dict | None) -> list[dict] | None:
        """Consumo reale dallo storico: material_N_amount (lunghezza estrusa, mm) con il GUID del materiale di ogni
        estrusore, convertito in grammi con densità e diametro del profilo del materiale."""
        uid = (snapshot or {}).get("uuid")
        if not uid:
            return None
        try:
            h = self._get(f"/api/v1/history/print_jobs/{uid}")
        except HubError:
            return None
        if not isinstance(h, dict):
            return None
        out = []
        for i in range(2):
            amount = h.get(f"material_{i}_amount") or 0
            if not isinstance(amount, (int, float)) or amount <= 0:
                continue
            m = self.material(h.get(f"material_{i}_guid") or "")
            grams = mm_to_grams(amount, m.get("diameter") or 2.85, m.get("density") or 1.24)
            out.append({"type": m.get("material") or "?", "brand": m.get("brand") or "", "color": m.get("color_code") or "",
                        "grams": round(grams, 1), "meters": round(amount / 1000, 2)})
        return out or None

    # ------------------------------------------------------------------ autorizzazione
    def request_auth(self, user: str) -> dict:
        """POST /api/v1/auth/request. Il formato dei parametri cambia tra firmware: la S5 accetta un modulo,
        i modelli recenti (S6, S8, Factor 4) possono volere multipart, JSON o parametri nell'indirizzo."""
        import requests as rq
        fields = {"application": "FleetBuddy", "user": user[:60] or "FleetBuddy"}
        attempts = (("form", {"data": fields}), ("multipart", {"files": {k: (None, v) for k, v in fields.items()}}),
                    ("json", {"json": fields}), ("query", {"params": fields}))
        last, tried = None, []
        for name, kw in attempts:
            try:
                r = self._s.post(self.base + "/api/v1/auth/request", timeout=8, **kw)
            except rq.RequestException as e:
                raise HubError(key="Stampante non raggiungibile: {e}", params={"e": e}) from e
            tried.append(f"{name} {r.status_code}")
            if r.status_code in (200, 201):
                try:
                    d = r.json()
                except ValueError:
                    d = {}
                if d.get("id") and d.get("key"):
                    return d
            last = r
        detail = ""
        if last is not None:
            try:
                j = last.json()
                detail = str(j.get("message") or j.get("error") or j)[:200] if isinstance(j, dict) else str(j)[:200]
            except ValueError:
                detail = last.text.strip()[:200]
        mode = self.probe().get("mode")
        if mode == "cloud":
            raise HubError(key="La stampante è in modalità di accesso \"cloud\" (UltiMaker Digital Factory) e non accetta autorizzazioni locali. Messaggio della stampante: {m}",
                           params={"m": detail or "—"})
        raise HubError(key="La stampante ha rifiutato la richiesta di autorizzazione ({t}). Messaggio della stampante: {m}",
                       params={"t": ", ".join(tried), "m": detail or "—"})

    def check_auth(self, auth_id: str) -> str:
        d = self._get(f"/api/v1/auth/check/{auth_id}") or {}
        return (d.get("message") if isinstance(d, dict) else str(d)) or "unknown"


def parse_material(xml: str) -> dict:
    """Legge un profilo materiale UltiMaker (fdmmaterial), ignorando gli spazi dei nomi XML."""
    import xml.etree.ElementTree as ET
    if not xml.strip().startswith("<"):
        return {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}

    def find(path: str) -> str:
        node = root
        for part in path.split("/"):
            node = next((c for c in list(node) if c.tag.split("}")[-1] == part), None) if node is not None else None
        return (node.text or "").strip() if node is not None else ""
    num = lambda v: float(v) if re.match(r"^\d+(\.\d+)?$", v or "") else None
    color = find("metadata/color_code")
    return {"brand": find("metadata/name/brand"), "material": find("metadata/name/material"),
            "color": find("metadata/name/color"), "color_code": color.upper()[:7] if color.startswith("#") else "",
            "guid": find("metadata/GUID"), "density": num(find("properties/density")),
            "diameter": num(find("properties/diameter"))}
