"""Calendario lavorativo: orari per giorno della settimana (con pause), festività nazionali, chiusure programmate.

Gli orari sono espressi nell'ora locale del fuso scelto (es. Europe/Rome): il passaggio tra ora legale e solare
è gestito da zoneinfo. Le festività arrivano dalla libreria "holidays" (oltre 150 paesi, anche con le festività
regionali o provinciali, es. i santi patroni in Italia). Il calendario è usato dalla Produzione:
  - "start":  le nuove stampe partono solo in orario lavorativo e possono finire fuori orario;
              anche il cambio piatto richiede una persona, quindi avviene in orario lavorativo;
  - "full":   le stampe devono iniziare e finire in orario lavorativo;
  - "always": il calendario è ignorato (24 ore su 24, 7 giorni su 7).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

log = logging.getLogger("fleetbuddy.workcal")

# paesi proposti per primi, con il nome in italiano e il fuso predefinito (gli altri restano selezionabili per codice)
COUNTRIES = {
    "IT": ("Italia", "Europe/Rome"), "SM": ("San Marino", "Europe/San_Marino"), "CH": ("Svizzera", "Europe/Zurich"),
    "DE": ("Germania", "Europe/Berlin"), "AT": ("Austria", "Europe/Vienna"), "FR": ("Francia", "Europe/Paris"),
    "ES": ("Spagna", "Europe/Madrid"), "PT": ("Portogallo", "Europe/Lisbon"), "GB": ("Regno Unito", "Europe/London"),
    "IE": ("Irlanda", "Europe/Dublin"), "NL": ("Paesi Bassi", "Europe/Amsterdam"), "BE": ("Belgio", "Europe/Brussels"),
    "LU": ("Lussemburgo", "Europe/Luxembourg"), "DK": ("Danimarca", "Europe/Copenhagen"), "SE": ("Svezia", "Europe/Stockholm"),
    "NO": ("Norvegia", "Europe/Oslo"), "FI": ("Finlandia", "Europe/Helsinki"), "PL": ("Polonia", "Europe/Warsaw"),
    "CZ": ("Repubblica Ceca", "Europe/Prague"), "SK": ("Slovacchia", "Europe/Bratislava"), "HU": ("Ungheria", "Europe/Budapest"),
    "SI": ("Slovenia", "Europe/Ljubljana"), "HR": ("Croazia", "Europe/Zagreb"), "RO": ("Romania", "Europe/Bucharest"),
    "GR": ("Grecia", "Europe/Athens"), "MT": ("Malta", "Europe/Malta"), "US": ("Stati Uniti", "America/New_York"),
    "CA": ("Canada", "America/Toronto"), "BR": ("Brasile", "America/Sao_Paulo"), "MX": ("Messico", "America/Mexico_City"),
    "AU": ("Australia", "Australia/Sydney"), "JP": ("Giappone", "Asia/Tokyo"), "CN": ("Cina", "Asia/Shanghai"),
    "IN": ("India", "Asia/Kolkata"), "IL": ("Israele", "Asia/Jerusalem"), "TR": ("Turchia", "Europe/Istanbul"),
}

DEFAULT = {
    "mode": "always",                       # finché non lo si attiva, la produzione lavora 24/7 come prima
    "country": "IT", "subdiv": "", "timezone": "Europe/Rome", "use_holidays": True,
    "hours": {str(d): ([["09:00", "13:00"], ["14:00", "18:00"]] if d < 5 else []) for d in range(7)},   # 0 = lunedì
    "closures": [],                          # [{"from": "2026-08-10", "to": "2026-08-21", "note": "Ferie"}]
    "allow_offhours_start": False,           # fuori orario un operatore presente può avviare il pezzo successivo
}
def N_(s: str) -> str:
    """Segna una frase da tradurre."""
    return s


TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")


def countries() -> list[dict]:
    import holidays
    supported = holidays.list_supported_countries()
    first = [{"code": c, "name": n, "timezone": tz, "subdivs": supported.get(c, [])} for c, (n, tz) in COUNTRIES.items() if c in supported]
    rest = [{"code": c, "name": c, "timezone": "", "subdivs": s} for c, s in sorted(supported.items()) if c not in COUNTRIES]
    return first + rest


def timezones() -> list[str]:
    return sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")))


def validate(cfg: dict) -> dict:
    """Configurazione pulita e completa (valori mancanti presi da DEFAULT); solleva ValueError con una frase da tradurre."""
    out = {**DEFAULT, **{k: v for k, v in (cfg or {}).items() if k in DEFAULT}}
    if out["mode"] not in ("start", "full", "always"):
        out["mode"] = "always"
    try:
        ZoneInfo(out["timezone"])
    except Exception as e:  # noqa: BLE001
        raise ValueError(N_("Fuso orario non valido")) from e
    hours = {}
    for d in range(7):
        iv = []
        for a, b in (out["hours"].get(str(d)) or []):
            if not (TIME_RE.match(a or "") and TIME_RE.match(b or "")) or a >= b:
                raise ValueError(N_("Orari non validi: ogni intervallo deve avere un inizio precedente alla fine"))
            iv.append([a, b])
        iv.sort()
        if any(iv[i][1] > iv[i + 1][0] for i in range(len(iv) - 1)):
            raise ValueError(N_("Orari non validi: gli intervalli dello stesso giorno si sovrappongono"))
        hours[str(d)] = iv
    out["hours"] = hours
    closures = []
    for c in out["closures"] or []:
        try:
            a, b = dt.date.fromisoformat(c["from"]), dt.date.fromisoformat(c.get("to") or c["from"])
        except (KeyError, ValueError) as e:
            raise ValueError(N_("Date di chiusura non valide")) from e
        if b < a:
            a, b = b, a
        closures.append({"from": a.isoformat(), "to": b.isoformat(), "note": str(c.get("note") or "")[:80]})
    out["closures"] = sorted(closures, key=lambda c: c["from"])
    out["allow_offhours_start"] = bool(out.get("allow_offhours_start"))
    out["subdiv"] = str(out.get("subdiv") or "")
    out["country"] = str(out.get("country") or "IT").upper()
    return out


@lru_cache(maxsize=64)
def _holidays(country: str, subdiv: str, year: int) -> dict:
    import holidays
    try:
        return dict(holidays.country_holidays(country, subdiv=subdiv or None, years=year))
    except Exception as e:  # noqa: BLE001 - paese o regione non supportati
        log.warning("Festività non disponibili per %s/%s: %s", country, subdiv, e)
        return {}


class WorkCalendar:
    def __init__(self, cfg: dict | None):
        self.cfg = validate(cfg or {})
        self.tz = ZoneInfo(self.cfg["timezone"])
        self.mode = self.cfg["mode"]
        self._closures = [(dt.date.fromisoformat(c["from"]), dt.date.fromisoformat(c["to"]), c["note"]) for c in self.cfg["closures"]]

    # ------------------------------------------------------------------ giorni
    def holiday(self, day: dt.date) -> str | None:
        if not self.cfg["use_holidays"]:
            return None
        return _holidays(self.cfg["country"], self.cfg["subdiv"], day.year).get(day)

    def closure(self, day: dt.date) -> str | None:
        for a, b, note in self._closures:
            if a <= day <= b:
                return note or "Chiusura"
        return None

    def day_off_reason(self, day: dt.date) -> str | None:
        """Motivo per cui il giorno è interamente non lavorativo (festività o chiusura), altrimenti None."""
        return self.closure(day) or self.holiday(day)

    def intervals(self, day: dt.date) -> list[tuple[float, float]]:
        """Intervalli lavorativi del giorno, come istanti (epoch). Vuoto nei giorni di chiusura o festivi."""
        if self.day_off_reason(day):
            return []
        out = []
        for a, b in self.cfg["hours"].get(str(day.weekday()), []):
            s = dt.datetime.combine(day, dt.time.fromisoformat(a), self.tz)
            e = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), self.tz) if b == "24:00" \
                else dt.datetime.combine(day, dt.time.fromisoformat(b), self.tz)
            out.append((s.timestamp(), e.timestamp()))
        return out

    def _day(self, ts: float) -> dt.date:
        return dt.datetime.fromtimestamp(ts, self.tz).date()

    def _iter_intervals(self, ts: float, days: int = 400):
        day = self._day(ts) - dt.timedelta(days=1)
        for _ in range(days):
            for s, e in self.intervals(day):
                if e > ts:
                    yield s, e
            day += dt.timedelta(days=1)

    # ------------------------------------------------------------------ istanti
    def has_hours(self) -> bool:
        return any(self.cfg["hours"].get(str(d)) for d in range(7))

    def active(self) -> bool:
        return self.mode != "always" and self.has_hours()

    def is_working(self, ts: float) -> bool:
        if not self.active():
            return True
        return any(s <= ts < e for s, e in self.intervals(self._day(ts)))

    def interval_end(self, ts: float) -> float | None:
        """Fine dell'intervallo lavorativo che contiene ts (None se ts è fuori orario)."""
        for s, e in self.intervals(self._day(ts)):
            if s <= ts < e:
                return e
        return None

    def next_working(self, ts: float) -> float:
        """Primo istante lavorativo a partire da ts (ts stesso se è in orario)."""
        if not self.active():
            return ts
        for s, e in self._iter_intervals(ts):
            return max(s, ts)
        return ts                                    # nessun orario nei prossimi 400 giorni: si ignora il calendario

    def start_for(self, ts: float, duration: float) -> float:
        """Inizio di una stampa che non può partire prima di ts, secondo la modalità del calendario."""
        if not self.active():
            return ts
        if self.mode == "start":
            return self.next_working(ts)
        for s, e in self._iter_intervals(ts):           # "full": deve stare tutta dentro un intervallo
            start = max(s, ts)
            if start + duration <= e:
                return start
        return self.next_working(ts)                 # nessun intervallo abbastanza lungo: si parte appena possibile

    def can_start_now(self, now: float, duration: float) -> bool:
        if not self.active():
            return True
        if not self.is_working(now):
            return False
        if self.mode == "full":
            end = self.interval_end(now)
            longest = max((e - s for d in range(7) for s, e in self.intervals(self._day(now) + dt.timedelta(days=d))), default=0)
            return end is not None and (now + duration <= end or duration > longest)
        return True

    def closed_spans(self, t0: float, t1: float) -> list[dict]:
        """Periodi non lavorativi tra t0 e t1 (complemento degli orari), con il motivo nei giorni festivi o di chiusura.
        Servono a ombreggiare il Gantt."""
        if not self.active():
            return []
        spans: list[dict] = []
        day = self._day(t0)
        while True:
            d0 = dt.datetime.combine(day, dt.time(0, 0), self.tz).timestamp()
            if d0 >= t1:
                break
            d1 = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), self.tz).timestamp()
            reason = self.day_off_reason(day) or ""
            cur = d0
            for s, e in self.intervals(day) + [(d1, d1)]:
                a, b = max(cur, t0), min(s, t1)
                if b > a:
                    if spans and a <= spans[-1]["end"] + 1:            # unione con il periodo precedente
                        spans[-1]["end"] = b
                        spans[-1]["label"] = spans[-1]["label"] or reason
                    else:
                        spans.append({"start": a, "end": b, "label": reason})
                cur = max(cur, e)
            day += dt.timedelta(days=1)
        return spans

    def upcoming_days_off(self, days: int = 365) -> list[dict]:
        """Festività e chiusure dei prossimi giorni (anteprima nelle impostazioni)."""
        today = dt.datetime.now(self.tz).date()
        out = []
        for i in range(days):
            d = today + dt.timedelta(days=i)
            c, h = self.closure(d), self.holiday(d)
            if c or h:
                out.append({"date": d.isoformat(), "name": c or h, "kind": "closure" if c else "holiday",
                            "weekday": d.weekday()})
        return out
