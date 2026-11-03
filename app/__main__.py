"""Avvio: python -m app  (legge listen, porta e certificati HTTPS da config.toml)."""
import uvicorn

from .config import load_config

cfg = load_config()
kw = {}
if cfg.app.tls_cert and cfg.app.tls_key:
    kw = {"ssl_certfile": str(cfg.path(cfg.app.tls_cert)), "ssl_keyfile": str(cfg.path(cfg.app.tls_key))}
uvicorn.run("app.main:app", host=cfg.app.listen, port=int(cfg.app.port), workers=1,
            access_log=False, proxy_headers=True, **kw)
