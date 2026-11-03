"""Caricamento configurazione da config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class HubCfg:
    host: str = "192.168.1.50"
    port: int = 8888
    sni_hostname: str = "fleet_v1.bambulab.com"
    client_cert: str = "certs/hub_client.chain.crt"
    client_key: str = "certs/hub_client.key"
    server_ca: str = "certs/root-ca.crt"
    user_name: str = "admin"
    password: str = ""
    password_hash_mode: str = "demo"


@dataclass
class CloudCfg:
    domain: str = "api.bambulab.com"
    activation_key: str = ""


@dataclass
class AppCfg:
    listen: str = "0.0.0.0"
    port: int = 8000
    poll_interval: float = 5
    data_dir: str = "data"
    ui_user: str = "admin"
    ui_password: str = ""
    snapshot_on_finish: bool = True
    tls_cert: str = ""
    tls_key: str = ""


@dataclass
class NotifyCfg:
    ntfy_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""
    on_finish: bool = True
    on_failed: bool = True
    on_error: bool = True
    on_queue_start: bool = False


@dataclass
class Config:
    hub: HubCfg = field(default_factory=HubCfg)
    cloud: CloudCfg = field(default_factory=CloudCfg)
    app: AppCfg = field(default_factory=AppCfg)
    notify: NotifyCfg = field(default_factory=NotifyCfg)

    def path(self, p: str) -> Path:
        """Risolve un percorso relativo rispetto alla cartella del progetto."""
        pp = Path(p)
        return pp if pp.is_absolute() else (BASE_DIR / pp)

    @property
    def data_path(self) -> Path:
        d = self.path(self.app.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d


def _fill(dc, values: dict | None):
    for k, v in (values or {}).items():
        if hasattr(dc, k):
            setattr(dc, k, v)
    return dc


def load_config(path: str | None = None) -> Config:
    path = path or os.environ.get("FLEETBUDDY_CONFIG") or str(BASE_DIR / "config.toml")
    cfg = Config()
    p = Path(path)
    if p.exists():
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
        for section in ("hub", "cloud", "app", "notify"):
            _fill(getattr(cfg, section), raw.get(section))
    return cfg
