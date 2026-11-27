# Client HTTP per l'API /slicer/v1 di Fabbrix Farm Manager, solo libreria standard (nessuna dipendenza in Cura).
import json
import ssl
import urllib.error
import urllib.request
import uuid

TIMEOUT = 30


class FarmError(Exception):
    def __init__(self, message, status=None, data=None):
        super().__init__(message)
        self.status = status
        self.data = data or {}


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


class FarmClient:
    def __init__(self, base_url, key="", insecure=False, user_agent="FabbrixFarmManager-Plugin/1.0"):
        self.base = normalize_url(base_url)
        self.key = key or ""
        self.insecure = bool(insecure)
        self.user_agent = user_agent

    def _ctx(self):
        if not self.base.startswith("https"):
            return None
        ctx = ssl.create_default_context()
        if self.insecure:                      # certificato autofirmato del Farm Manager (scelta dell'utente)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _call(self, method, path, body=None, headers=None, timeout=TIMEOUT):
        h = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.key:
            h["X-Api-Key"] = self.key
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx()) as r:
                raw = r.read()
                return r.status, (json.loads(raw.decode("utf-8")) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
            if e.code == 202:
                return 202, data
            raise FarmError(data.get("error") or data.get("detail") or "HTTP %s" % e.code, e.code, data)
        except urllib.error.URLError as e:
            raise FarmError(str(getattr(e, "reason", e)))

    def get(self, path):
        return self._call("GET", path)

    def post_json(self, path, obj):
        return self._call("POST", path, json.dumps(obj).encode("utf-8"), {"Content-Type": "application/json"})

    # ------------------------------------------------------------------ API
    def pair_request(self, app, device):
        return self.post_json("/slicer/v1/pair", {"app": app, "device": device})[1]

    def pair_status(self, request_id):
        return self.get("/slicer/v1/pair/" + request_id)

    def info(self):
        return self.get("/slicer/v1/info")[1]

    def upload(self, filename, data, fields, mime="application/octet-stream"):
        boundary = "----fabbrix" + uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode("utf-8"))
        safe = filename.replace('"', "'").replace("\r", " ").replace("\n", " ")
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                      % (boundary, safe, mime)).encode("utf-8"))
        parts.append(data)
        parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        body = b"".join(parts)
        return self._call("POST", "/slicer/v1/upload", body,
                          {"Content-Type": "multipart/form-data; boundary=" + boundary}, timeout=600)[1]
