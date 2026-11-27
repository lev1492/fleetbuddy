# /// script
# requires-python = ">=3.10"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Fabbrix Farm Manager"
# description = "Invia i file esportati a Fabbrix Farm Manager: cartella, destinazione, ordine di produzione con copie e priorità, note."
# author = "Fabbrix"
# version = "1.0.0"
# ///
"""Fabbrix Farm Manager – plugin per OrcaSlicer 2.5+.

Uso:
  1. File → Plugin → Installa plugin locale → questo file, poi spunta "Attiva".
  2. Esporta il G-code (o il file del piatto .gcode.3mf): compare l'avviso "Invia al Farm Manager".
     In alternativa: barra spaziatrice (Speed Dial) → "Invia a Fabbrix Farm Manager".
  3. La prima volta si inserisce l'indirizzo del Farm Manager e si approva il codice di abbinamento nel browser.

Usa solo API stabili di OrcaSlicer (script, eventi, finestre HTML) e la libreria standard di Python.
OrcaSlicer chiede una volta il permesso di leggere il file esportato e di collegarsi in rete.
"""
import html
import json
import os
import platform
import ssl
import urllib.error
import urllib.request
import uuid

import orca

PLUGIN_VERSION = "1.0.0"
NAME = "Fabbrix Farm Manager"

EN = {
    "Invia a Fabbrix Farm Manager": "Send to Fabbrix Farm Manager",
    "Invia al Farm Manager": "Send to Farm Manager",
    "Esportato: {f}": "Exported: {f}",
    "Collega OrcaSlicer al Farm Manager": "Connect OrcaSlicer to the Farm Manager",
    "Indirizzo del Farm Manager": "Farm Manager address",
    "Accetta certificati HTTPS autofirmati": "Accept self-signed HTTPS certificates",
    "Collega": "Connect",
    "Annulla": "Cancel",
    "Invia": "Send",
    "Scollega": "Disconnect",
    "Codice di abbinamento: {c}\n\nNel Farm Manager (già aperto nel browser, altrimenti vai su {u}) approva la richiesta con questo codice, poi premi OK.":
        "Pairing code: {c}\n\nIn the Farm Manager (already open in your browser, otherwise go to {u}) approve the request with this code, then press OK.",
    "In attesa di approvazione…": "Waiting for approval…",
    "Collegato come {u}": "Connected as {u}",
    "Abbinamento non riuscito: {e}": "Pairing failed: {e}",
    "Richiesta non ancora approvata: riprova.": "Request not approved yet: try again.",
    "File da inviare": "File to send",
    "Cartella dei Files": "Files folder",
    "Destinazione": "Destination",
    "Tutte le stampanti compatibili": "All compatible printers",
    "Gruppo {g}": "Group {g}",
    "Azione": "Action",
    "Solo carica nei Files": "Upload to Files only",
    "Carica e crea un ordine di produzione": "Upload and create a production order",
    "Copie": "Copies",
    "Priorità": "Priority",
    "Bassa": "Low", "Normale": "Normal", "Alta": "High", "Urgente": "Urgent",
    "Note": "Notes",
    "Stampante in OrcaSlicer": "Printer in OrcaSlicer",
    "Invio in corso…": "Sending…",
    "«{f}» è nei Files.": "“{f}” is in Files.",
    "«{f}» è nei Files ed è stato creato l'ordine di produzione.": "“{f}” is in Files and the production order has been created.",
    "«{f}» era già nei Files.": "“{f}” was already in Files.",
    "Gli ordini dagli slicer partono in pausa: avviali dalla Produzione.": "Orders from slicers start paused: start them from Production.",
    "Invio non riuscito: {e}": "Sending failed: {e}",
    "Esporta prima il G-code o il file del piatto, oppure indica il percorso del file.": "Export the G-code or plate file first, or enter the file path.",
    "File non trovato: {f}": "File not found: {f}",
    "Chiave non più valida: collega di nuovo il plugin.": "Key no longer valid: connect the plugin again.",
}


def _lang():
    try:
        return str(orca.host.app_language() or "en")[:2].lower()
    except Exception:  # noqa: BLE001
        return "en"


def tr(text, **params):
    out = text if _lang() == "it" else EN.get(text, text)
    for k, v in params.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ---------------------------------------------------------------------- client HTTP (/slicer/v1)
class FarmError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def _call(cfg, method, path, body=None, headers=None, with_key=True, timeout=30):
    base = normalize_url(cfg.get("url"))
    h = {"User-Agent": "OrcaSlicer FabbrixFarmManager-Orca/%s" % PLUGIN_VERSION, "Accept": "application/json"}
    if with_key and cfg.get("key"):
        h["X-Api-Key"] = cfg["key"]
    h.update(headers or {})
    ctx = None
    if base.startswith("https"):
        ctx = ssl.create_default_context()
        if cfg.get("insecure"):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(base + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode("utf-8")) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        raise FarmError(data.get("error") or data.get("detail") or "HTTP %s" % e.code, e.code)
    except urllib.error.URLError as e:
        raise FarmError(str(getattr(e, "reason", e)))


def _multipart(fields, filename, data):
    boundary = "----fabbrix" + uuid.uuid4().hex
    parts = [("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode("utf-8")
             for k, v in fields.items()]
    safe = filename.replace('"', "'").replace("\r", " ").replace("\n", " ")
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                  "Content-Type: application/octet-stream\r\n\r\n" % (boundary, safe)).encode("utf-8"))
    parts += [data, ("\r\n--%s--\r\n" % boundary).encode("utf-8")]
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


# ---------------------------------------------------------------------- finestre HTML
CSS = """<style>
body{font-family:var(--orca-font,sans-serif);background:var(--orca-bg,#fff);color:var(--orca-fg,#222);margin:0;padding:16px;font-size:13px}
h2{font-size:16px;margin:0 0 12px}label{display:block;margin:10px 0 4px;color:var(--orca-muted,#666)}
input,select,textarea{width:100%;box-sizing:border-box;padding:6px;border:1px solid var(--orca-border,#ccc);border-radius:4px;
background:var(--orca-bg,#fff);color:var(--orca-fg,#222);font:inherit}textarea{height:64px}
.row{display:flex;gap:8px;align-items:center}.row input[type=checkbox]{width:auto}
.foot{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}.foot .left{margin-right:auto}
button{padding:6px 14px;border:1px solid var(--orca-border,#ccc);border-radius:4px;background:var(--orca-bg,#fff);color:var(--orca-fg,#222);font:inherit;cursor:pointer}
button.primary{background:var(--orca-accent,#009688);color:var(--orca-accent-fg,#fff);border-color:var(--orca-accent,#009688)}
.muted{color:var(--orca-muted,#666);font-size:12px}
</style>"""


def _pair_html(cfg):
    return """<!doctype html><html><head><meta charset="utf-8">%s</head><body>
<h2>%s</h2>
<label>%s</label><input id="url" placeholder="http://192.168.1.20:8000" value="%s">
<div class="row" style="margin-top:8px"><input type="checkbox" id="ins" %s><span>%s</span></div>
<div class="foot"><button onclick="orca.close()">%s</button>
<button class="primary" onclick="orca.submit({url: document.getElementById('url').value, insecure: document.getElementById('ins').checked})">%s</button></div>
</body></html>""" % (CSS, html.escape(tr("Collega OrcaSlicer al Farm Manager")), html.escape(tr("Indirizzo del Farm Manager")),
                     html.escape(cfg.get("url") or ""), "checked" if cfg.get("insecure") else "",
                     html.escape(tr("Accetta certificati HTTPS autofirmati")), html.escape(tr("Annulla")),
                     html.escape(tr("Collega")))


def _send_html(cfg, info, path, machine):
    T = {k: tr(k) for k in ("Tutte le stampanti compatibili", "Gruppo {g}", "Solo carica nei Files",
                            "Carica e crea un ordine di produzione", "Bassa", "Normale", "Alta", "Urgente")}
    data = {"info": info, "last": {"project": cfg.get("last_project", ""), "target": cfg.get("last_target", ""),
                                   "action": cfg.get("last_action", "upload")}, "T": T}
    return """<!doctype html><html><head><meta charset="utf-8">%(css)s</head><body>
<h2>%(title)s</h2>
<label>%(l_file)s</label><input id="path" value="%(path)s">
<div class="muted">%(l_machine)s: %(machine)s</div>
<label>%(l_proj)s</label><select id="proj"></select>
<label>%(l_dest)s</label><select id="dest"></select>
<label>%(l_act)s</label><select id="act"></select>
<div id="orderopts"><div class="row"><div style="flex:1"><label>%(l_cop)s</label><input id="copies" type="number" min="1" max="999" value="1"></div>
<div style="flex:1" id="priobox"><label>%(l_prio)s</label><select id="prio"></select></div></div></div>
<label>%(l_notes)s</label><textarea id="notes"></textarea>
<div class="muted" style="margin-top:10px">%(who)s</div>
<div class="foot"><button class="left" onclick="orca.submit({cmd:'unpair'})">%(b_unpair)s</button>
<button onclick="orca.close()">%(b_cancel)s</button><button class="primary" onclick="send()">%(b_send)s</button></div>
<script>
const D = %(data)s, I = D.info, T = D.T;
const $ = id => document.getElementById(id);
function fill(sel, items, want, def) {
  sel.innerHTML = ""; for (const [v, t] of items) { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.append(o); }
  sel.value = items.some(x => x[0] === want) ? want : (items.some(x => x[0] === def) ? def : (items[0] || [""])[0]);
}
fill($("proj"), I.projects.map(p => [p.id === null ? "none" : String(p.id), p.path]), D.last.project,
     I.default_project === null || I.default_project === undefined ? "" : String(I.default_project));
fill($("dest"), [["", T["Tutte le stampanti compatibili"]], ...I.groups.map(g => ["g:" + g.id, T["Gruppo {g}"].replace("{g}", g.name)]),
     ...I.printers.map(p => ["p:" + p.sn, p.name + (p.model ? " (" + p.model + ")" : "")])], D.last.target, "");
fill($("act"), I.perms.order ? [["upload", T["Solo carica nei Files"]], ["order", T["Carica e crea un ordine di produzione"]]]
     : [["upload", T["Solo carica nei Files"]]], D.last.action, "upload");
fill($("prio"), [["0", T["Bassa"]], ["1", T["Normale"]], ["2", T["Alta"]], ["3", T["Urgente"]]], "1", "1");
$("priobox").style.display = I.perms.prioritize ? "" : "none";
const sync = () => { $("orderopts").style.display = $("act").value === "order" ? "" : "none"; };
$("act").onchange = sync; sync();
function send() {
  orca.submit({cmd: "send", path: $("path").value, project_id: $("proj").value, target: $("dest").value, action: $("act").value,
               copies: parseInt($("copies").value || "1", 10), priority: parseInt($("prio").value, 10), notes: $("notes").value});
}
</script></body></html>""" % {
        "css": CSS, "title": html.escape(tr("Invia a Fabbrix Farm Manager")), "l_file": html.escape(tr("File da inviare")),
        "path": html.escape(path or ""), "l_machine": html.escape(tr("Stampante in OrcaSlicer")), "machine": html.escape(machine or "—"),
        "l_proj": html.escape(tr("Cartella dei Files")), "l_dest": html.escape(tr("Destinazione")), "l_act": html.escape(tr("Azione")),
        "l_cop": html.escape(tr("Copie")), "l_prio": html.escape(tr("Priorità")), "l_notes": html.escape(tr("Note")),
        "who": html.escape(tr("Collegato come {u}", u=info.get("user", "")) + " · " + (cfg.get("url") or "")),
        "b_unpair": html.escape(tr("Scollega")), "b_cancel": html.escape(tr("Annulla")), "b_send": html.escape(tr("Invia")),
        "data": json.dumps(data).replace("</", "<\\/"),
    }


# ---------------------------------------------------------------------- capacità "script"
def _is_event(event, name):
    target = getattr(orca, name, None)
    if target is None:
        target = getattr(getattr(orca, "LifecycleEvent", None), name, None)
    return (target is not None and event == target) or str(getattr(event, "name", event)).endswith(name)


def _notify_level():
    lv = getattr(orca.host.ui, "NotificationLevel", None)
    if lv is None:
        return None
    names = [n for n in dir(lv) if not n.startswith("_")]
    pick = next((n for n in names if "info" in n.lower() or "regular" in n.lower()), names[0] if names else None)
    return getattr(lv, pick) if pick else None


class FabbrixSend(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Invia a Fabbrix Farm Manager"

    # -------------------------------------------------------------- configurazione (url, chiave, ultime scelte)
    def get_default_config(self):
        return {"url": "", "key": "", "user": "", "insecure": False, "last_project": "", "last_target": "",
                "last_action": "upload", "last_file": ""}

    def _cfg(self):
        try:
            raw = self.get_config()
            cfg = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        except Exception:  # noqa: BLE001
            cfg = {}
        return {**self.get_default_config(), **(cfg if isinstance(cfg, dict) else {})}

    def _save(self, cfg):
        try:
            self.save_config(json.dumps(cfg))
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- eventi: file appena esportato
    def on_lifecycle_event(self, event, ctx):
        if not _is_event(event, "GCodeExportFinished"):
            return
        ok = getattr(getattr(orca, "LifecycleEvtCode", None), "Ok", None)
        if ok is not None and getattr(ctx, "code", ok) != ok:
            return
        path = str(getattr(ctx, "msg", "") or "")
        if not path:
            return
        cfg = self._cfg()
        cfg["last_file"] = path
        self._save(cfg)
        try:
            orca.host.ui.push_notification(_notify_level(), tr("Esportato: {f}", f=os.path.basename(path)),
                                           hyper_text=tr("Invia al Farm Manager"), on_click=lambda *a: self.send_file(path))
        except Exception:  # noqa: BLE001 - l'avviso è una comodità: resta la Speed Dial
            pass

    def execute(self):
        return self.send_file(self._cfg().get("last_file") or "")

    # -------------------------------------------------------------- abbinamento
    def _pair(self, cfg):
        res = orca.host.ui.show_dialog(html=_pair_html(cfg), title=NAME, width=460, height=280)
        if not res or not res.get("url"):
            return None
        cfg.update(url=normalize_url(res["url"]), insecure=bool(res.get("insecure")), key="", user="")
        self._save(cfg)
        try:
            _st, req = _call(cfg, "POST", "/slicer/v1/pair",
                             json.dumps({"app": "OrcaSlicer", "device": platform.node() or "PC"}).encode("utf-8"),
                             {"Content-Type": "application/json"}, with_key=False)
        except FarmError as e:
            orca.host.ui.message(tr("Abbinamento non riuscito: {e}", e=e), title=NAME, icon="error")
            return None
        try:
            import webbrowser
            webbrowser.open(req["approve_url"])
        except Exception:  # noqa: BLE001 - se non si apre, l'indirizzo è scritto nel messaggio
            pass
        while True:
            answer = orca.host.ui.message(tr("Codice di abbinamento: {c}\n\nNel Farm Manager (già aperto nel browser, altrimenti vai su {u}) approva la richiesta con questo codice, poi premi OK.",
                                             c=req["code"], u=req["approve_url"]), title=NAME, buttons="ok_cancel", icon="info")
            if answer != "ok":
                return None
            try:
                status, data = _call(cfg, "GET", "/slicer/v1/pair/" + req["request_id"], with_key=False)
            except FarmError as e:
                orca.host.ui.message(tr("Abbinamento non riuscito: {e}", e=e), title=NAME, icon="error")
                return None
            if status == 200 and data.get("key"):
                cfg.update(key=data["key"], user=data.get("user") or "")
                self._save(cfg)
                return cfg
            orca.host.ui.message(tr("Richiesta non ancora approvata: riprova."), title=NAME, icon="warning")

    # -------------------------------------------------------------- invio
    def _machine(self):
        try:
            preset = orca.host.preset_bundle().current_printer_preset()
            model = preset.config_value("printer_model") or ""
            return preset.name + (" (%s)" % model if model and model not in preset.name else "")
        except Exception:  # noqa: BLE001
            return ""

    def send_file(self, path):
        cfg = self._cfg()
        if not cfg.get("key"):
            cfg = self._pair(cfg)
            if not cfg:
                return orca.ExecutionResult.skipped("")
        try:
            _st, info = _call(cfg, "GET", "/slicer/v1/info")
        except FarmError as e:
            if e.status == 403:
                cfg.update(key="", user="")
                self._save(cfg)
                orca.host.ui.message(tr("Chiave non più valida: collega di nuovo il plugin."), title=NAME, icon="warning")
                return orca.ExecutionResult.skipped("")
            orca.host.ui.message(tr("Invio non riuscito: {e}", e=e), title=NAME, icon="error")
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, str(e))
        res = orca.host.ui.show_dialog(html=_send_html(cfg, info, path, self._machine()), title=NAME, width=520, height=620)
        if not res:
            return orca.ExecutionResult.skipped("")
        if res.get("cmd") == "unpair":
            cfg.update(key="", user="")
            self._save(cfg)
            return orca.ExecutionResult.skipped("")
        path = str(res.get("path") or "").strip().strip('"')
        if not path:
            orca.host.ui.message(tr("Esporta prima il G-code o il file del piatto, oppure indica il percorso del file."), title=NAME, icon="warning")
            return orca.ExecutionResult.skipped("")
        if not os.path.isfile(path):
            orca.host.ui.message(tr("File non trovato: {f}", f=path), title=NAME, icon="error")
            return orca.ExecutionResult.skipped("")
        cfg.update(last_project=str(res.get("project_id") or ""), last_target=str(res.get("target") or ""),
                   last_action=str(res.get("action") or "upload"), last_file=path)
        self._save(cfg)
        fields = {"project_id": cfg["last_project"], "target": cfg["last_target"], "action": cfg["last_action"],
                  "copies": str(max(1, int(res.get("copies") or 1))), "priority": str(int(res.get("priority", 1))),
                  "notes": str(res.get("notes") or "")}
        progress = None
        try:
            progress = orca.host.ui.create_progress_dialog(NAME, tr("Invio in corso…"))
            if hasattr(progress, "pulse"):
                progress.pulse()
        except Exception:  # noqa: BLE001
            progress = None
        try:
            with open(path, "rb") as fh:
                body, ctype = _multipart(fields, os.path.basename(path), fh.read())
            _st, out = _call(cfg, "POST", "/slicer/v1/upload", body, {"Content-Type": ctype}, timeout=600)
        except FarmError as e:
            self._close(progress)
            orca.host.ui.message(tr("Invio non riuscito: {e}", e=e), title=NAME, icon="error")
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, str(e))
        self._close(progress)
        f = out.get("filename") or os.path.basename(path)
        if out.get("production_id"):
            text = tr("«{f}» è nei Files ed è stato creato l'ordine di produzione.", f=f)
            if info.get("orders_paused"):
                text += "\n" + tr("Gli ordini dagli slicer partono in pausa: avviali dalla Produzione.")
        elif out.get("duplicate"):
            text = tr("«{f}» era già nei Files.", f=f)
        else:
            text = tr("«{f}» è nei Files.", f=f)
        orca.host.ui.message(text, title=NAME, icon="info")
        return orca.ExecutionResult.success(text)

    @staticmethod
    def _close(progress):
        for name in ("close", "destroy", "finish"):
            fn = getattr(progress, name, None) if progress is not None else None
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
                return


@orca.plugin
class FabbrixFarmManagerPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(FabbrixSend)
