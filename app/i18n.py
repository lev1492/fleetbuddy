"""Traduzioni di FleetBuddy.

Il testo di riferimento è l'italiano: nel codice ogni frase è scritta in italiano e i file
static/i18n/<lingua>.json contengono le traduzioni ("frase italiana": "traduzione").
Gli stessi file vengono usati dal browser, così server e interfaccia restano allineati.
Segnaposto: {nome}. Per trovare le frasi mancanti: python -m tools.i18n_check
"""
from __future__ import annotations

import contextvars
import json
from pathlib import Path

LANGS = {"it": "Italiano", "en": "English", "fr": "Français", "de": "Deutsch", "es": "Español", "pt": "Português"}
DEFAULT_LANG = "it"
CATALOG_DIR = Path(__file__).resolve().parent.parent / "static" / "i18n"
current_lang: contextvars.ContextVar[str] = contextvars.ContextVar("fleetbuddy_lang", default=DEFAULT_LANG)
_CACHE: dict[str, dict[str, str]] = {}


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def catalog(lang: str) -> dict[str, str]:
    if lang not in _CACHE:
        p = CATALOG_DIR / f"{lang}.json"
        try:
            _CACHE[lang] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except ValueError:
            _CACHE[lang] = {}
    return _CACHE[lang]


def normalize(code: str | None) -> str | None:
    """Codice lingua supportato ('it', 'en', ...) oppure None."""
    if not code:
        return None
    c = str(code).strip().lower()[:2]
    return c if c in LANGS else None


def from_accept_language(header: str | None) -> str:
    for part in (header or "").split(","):
        c = normalize(part.split(";")[0])
        if c:
            return c
    return DEFAULT_LANG


def tr(lang: str | None, text: str, **params) -> str:
    lang = normalize(lang) or DEFAULT_LANG
    # "testo|contesto": il contesto distingue parole uguali in italiano ma diverse in altre lingue
    out = (None if lang == DEFAULT_LANG else catalog(lang).get(text)) or text.split("|")[0]
    return out.format_map(_Keep({k: str(v) for k, v in params.items()})) if params else out


def _(text: str, **params) -> str:
    """Traduce nella lingua della richiesta in corso."""
    return tr(current_lang.get(), text, **params)
