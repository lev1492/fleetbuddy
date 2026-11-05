"""Connettori per le stampanti collegate direttamente (senza Fleet Hub)."""
from .base import DirectClient
from .duet import DuetClient
from .elegoo import ElegooClient
from .moonraker import MoonrakerClient
from .octoprint import OctoPrintClient
from .prusalink import PrusaLinkClient
from .raise3d import Raise3DClient
from .repetier import RepetierClient

KINDS: dict[str, type[DirectClient]] = {c.kind: c for c in (
    OctoPrintClient, MoonrakerClient, PrusaLinkClient, DuetClient, RepetierClient, ElegooClient, Raise3DClient)}


def make_client(row: dict) -> DirectClient:
    return KINDS[row["kind"]](row)
