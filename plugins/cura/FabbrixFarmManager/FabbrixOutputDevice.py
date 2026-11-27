# Fabbrix Farm Manager – dispositivo di uscita per Cura.
#
# "Invia a Fabbrix Farm Manager" compare accanto a "Salva su disco" per qualsiasi stampante. Alla prima volta si
# abbina il plugin al Farm Manager con un codice (nessuna chiave da copiare); poi una finestra fa scegliere cartella,
# destinazione, azione (solo Files / ordine di produzione), copie, priorità e note.
# Le stampanti che leggono .ufp (UltiMaker) ricevono un UFP, le altre un G-code.
import os
import platform
import tempfile
import threading
import time

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices

from UM.Logger import Logger
from UM.Mesh.MeshWriter import MeshWriter
from UM.Message import Message
from UM.OutputDevice import OutputDeviceError
from UM.OutputDevice.OutputDevice import OutputDevice
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin

from cura.CuraApplication import CuraApplication

from .FarmClient import FarmClient, FarmError, normalize_url

PLUGIN_VERSION = "1.0.0"
PREF = "fabbrix_farm_manager/"

# Testi: italiano e inglese (lingua di Cura); le altre lingue usano l'inglese.
EN = {
    "Invia a Fabbrix Farm Manager": "Send to Fabbrix Farm Manager",
    "Invia al Farm Manager": "Send to Farm Manager",
    "Invia il file sezionato a Fabbrix Farm Manager": "Send the sliced file to Fabbrix Farm Manager",
    "Collega Cura al Farm Manager": "Connect Cura to the Farm Manager",
    "Indirizzo del Farm Manager": "Farm Manager address",
    "Accetta certificati HTTPS autofirmati": "Accept self-signed HTTPS certificates",
    "Collega": "Connect",
    "Annulla": "Cancel",
    "Chiudi": "Close",
    "Invia": "Send",
    "Scollega": "Disconnect",
    "Approva nel Farm Manager il codice": "In the Farm Manager approve the code",
    "Si è aperto il browser: entra nel Farm Manager e approva la richiesta. Se non si è aperto, vai su": "Your browser has opened: sign in to the Farm Manager and approve the request. If it did not open, go to",
    "In attesa di approvazione…": "Waiting for approval…",
    "Collegato come {u}": "Connected as {u}",
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
    "File": "File",
    "Stampante in Cura": "Printer in Cura",
    "Caricamento delle informazioni…": "Loading information…",
    "Invio in corso…": "Sending…",
    "Inviato a Fabbrix Farm Manager": "Sent to Fabbrix Farm Manager",
    "«{f}» è nei Files.": "“{f}” is in Files.",
    "«{f}» è nei Files ed è stato creato l'ordine di produzione.": "“{f}” is in Files and the production order has been created.",
    "«{f}» era già nei Files.": "“{f}” was already in Files.",
    "Apri il Farm Manager": "Open the Farm Manager",
    "Invio non riuscito": "Sending failed",
    "Niente da inviare: esegui prima lo slicing.": "Nothing to send: slice first.",
    "Il tuo ruolo non consente di creare ordini di produzione.": "Your role does not allow creating production orders.",
    "Gli ordini dagli slicer partono in pausa: avviali dalla Produzione.": "Orders from slicers start paused: start them from Production.",
    "Abbinamento non riuscito": "Pairing failed",
    "Richiesta scaduta: riprova.": "Request expired: try again.",
}


def _lang():
    try:
        return str(CuraApplication.getInstance().getPreferences().getValue("general/language") or "en")[:2].lower()
    except Exception:  # noqa: BLE001
        return "en"


def tr(text, **params):
    out = text if _lang() == "it" else EN.get(text, text)
    for k, v in params.items():
        out = out.replace("{" + k + "}", str(v))
    return out


class FabbrixOutputDevicePlugin(OutputDevicePlugin):
    def __init__(self):
        super().__init__()
        self._device = None

    def start(self):
        prefs = CuraApplication.getInstance().getPreferences()
        for key, default in (("url", ""), ("key", ""), ("user", ""), ("insecure", False), ("last_project", ""),
                             ("last_target", ""), ("last_action", "upload")):
            prefs.addPreference(PREF + key, default)
        if self._device is None:
            self._device = FabbrixOutputDevice()
        self.getOutputDeviceManager().addOutputDevice(self._device)

    def stop(self):
        if self._device is not None:
            self.getOutputDeviceManager().removeOutputDevice(self._device.getId())


class FabbrixOutputDevice(QObject, OutputDevice):
    infoChanged = pyqtSignal()
    stateChanged = pyqtSignal()

    def __init__(self, parent=None):
        # ereditarietà cooperativa di PyQt (come PrinterOutputDevice di Cura): QObject passa device_id a OutputDevice
        super().__init__(device_id="fabbrix_farm_manager", parent=parent)
        self.setName("Fabbrix Farm Manager")
        self.setShortDescription(tr("Invia al Farm Manager"))
        self.setDescription(tr("Invia il file sezionato a Fabbrix Farm Manager"))
        self.setIconName("print")
        self.setPriority(1)
        self._dialog = None
        self._nodes = None
        self._file_handler = None
        self._info = {}
        self._status = ""
        self._busy = False
        self._pair = None            # {request_id, code, approve_url}
        self._pair_stop = None

    # ------------------------------------------------------------------ preferenze
    def _pref(self, key):
        return CuraApplication.getInstance().getPreferences().getValue(PREF + key)

    def _set_pref(self, key, value):
        CuraApplication.getInstance().getPreferences().setValue(PREF + key, value)

    def _client(self, with_key=True):
        app = CuraApplication.getInstance()
        ua = "Cura/%s FabbrixFarmManager-Cura/%s" % (app.getVersion(), PLUGIN_VERSION)
        return FarmClient(self._pref("url"), self._pref("key") if with_key else "", bool(self._pref("insecure")), ua)

    def _later(self, fn, *args):
        CuraApplication.getInstance().callLater(fn, *args)

    def _run(self, work, done, fail):
        """Esegue work() in un thread e richiama done(risultato) o fail(errore) nel thread dell'interfaccia."""
        def target():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                Logger.log("w", "Fabbrix Farm Manager: %s", e)
                self._later(fail, e)
                return
            self._later(done, res)
        threading.Thread(target=target, daemon=True).start()

    # ------------------------------------------------------------------ proprietà per la finestra (QML)
    @pyqtSlot(str, result=str)
    def t(self, text):
        return tr(text)

    @pyqtProperty(str, notify=stateChanged)
    def serverUrl(self):
        return self._pref("url") or ""

    @pyqtProperty(bool, notify=stateChanged)
    def insecure(self):
        return bool(self._pref("insecure"))

    @pyqtProperty(bool, notify=stateChanged)
    def paired(self):
        return bool(self._pref("key"))

    @pyqtProperty(str, notify=stateChanged)
    def userName(self):
        return self._pref("user") or ""

    @pyqtProperty(str, notify=stateChanged)
    def status(self):
        return self._status

    @pyqtProperty(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @pyqtProperty(str, notify=stateChanged)
    def pairCode(self):
        return (self._pair or {}).get("code", "")

    @pyqtProperty(str, notify=stateChanged)
    def pairUrl(self):
        return (self._pair or {}).get("approve_url", "")

    @pyqtProperty("QVariantMap", notify=infoChanged)
    def info(self):
        return self._info

    @pyqtProperty(str, notify=stateChanged)
    def lastProject(self):
        return self._pref("last_project") or ""

    @pyqtProperty(str, notify=stateChanged)
    def lastTarget(self):
        return self._pref("last_target") or ""

    @pyqtProperty(str, notify=stateChanged)
    def lastAction(self):
        return self._pref("last_action") or "upload"

    @pyqtProperty(str, notify=stateChanged)
    def fileName(self):
        return self._file_name()[0]

    @pyqtProperty(str, notify=stateChanged)
    def machineName(self):
        stack = CuraApplication.getInstance().getGlobalContainerStack()
        return stack.getName() if stack else ""

    def _set(self, status=None, busy=None):
        if status is not None:
            self._status = status
        if busy is not None:
            self._busy = busy
        self.stateChanged.emit()

    # ------------------------------------------------------------------ OutputDevice
    def requestWrite(self, nodes, file_name=None, limit_mimetypes=None, file_handler=None, filter_by_machine=False, **kwargs):
        if not nodes:
            raise OutputDeviceError.WriteRequestFailedError(tr("Niente da inviare: esegui prima lo slicing."))
        self._nodes = nodes
        self._file_handler = file_handler or CuraApplication.getInstance().getMeshFileHandler()
        self._set(status="")
        self._open_dialog()
        if self.paired:
            self.refreshInfo()

    def _open_dialog(self):
        if self._dialog is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml", "SendDialog.qml")
            self._dialog = CuraApplication.getInstance().createQmlComponent(path, {"manager": self})
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.requestActivate()

    @pyqtSlot()
    def closeDialog(self):
        self.cancelPairing()
        if self._dialog is not None:
            self._dialog.close()

    # ------------------------------------------------------------------ abbinamento
    @pyqtSlot(str, bool)
    def startPairing(self, url, insecure):
        url = normalize_url(url)
        if not url:
            return
        self._set_pref("url", url)
        self._set_pref("insecure", bool(insecure))
        self.cancelPairing()
        self._set(status=tr("In attesa di approvazione…"), busy=True)
        device = platform.node() or "PC"

        def work():
            return self._client(with_key=False).pair_request("Cura", device)

        def done(res):
            self._pair = res
            self._set(busy=False)
            QDesktopServices.openUrl(QUrl(res.get("approve_url", "")))
            self._poll_pairing(res["request_id"])

        self._run(work, done, lambda e: self._set(status="%s: %s" % (tr("Abbinamento non riuscito"), e), busy=False))

    def _poll_pairing(self, request_id):
        stop = threading.Event()
        self._pair_stop = stop
        client = self._client(with_key=False)

        def loop():
            deadline = time.time() + 600
            while not stop.is_set() and time.time() < deadline:
                try:
                    status, data = client.pair_status(request_id)
                except FarmError as e:
                    self._later(self._pair_failed, str(e))
                    return
                except Exception as e:  # noqa: BLE001 - rete assente per un attimo: si riprova
                    Logger.log("w", "Fabbrix Farm Manager: %s", e)
                    status, data = 202, {}
                if status == 200 and data.get("key"):
                    self._later(self._paired, data)
                    return
                stop.wait(2)
            if not stop.is_set():
                self._later(self._pair_failed, tr("Richiesta scaduta: riprova."))
        threading.Thread(target=loop, daemon=True).start()

    def _paired(self, data):
        self._set_pref("key", data["key"])
        self._set_pref("user", data.get("user") or "")
        self._pair = None
        self._set(status=tr("Collegato come {u}", u=data.get("user") or ""))
        self.refreshInfo()

    def _pair_failed(self, msg):
        self._pair = None
        self._set(status="%s: %s" % (tr("Abbinamento non riuscito"), msg), busy=False)

    @pyqtSlot()
    def cancelPairing(self):
        if self._pair_stop is not None:
            self._pair_stop.set()
            self._pair_stop = None
        if self._pair is not None:
            self._pair = None
            self._set(status="")

    @pyqtSlot()
    def unpair(self):
        self._set_pref("key", "")
        self._set_pref("user", "")
        self._info = {}
        self.infoChanged.emit()
        self._set(status="")

    # ------------------------------------------------------------------ informazioni e invio
    @pyqtSlot()
    def refreshInfo(self):
        self._set(status=tr("Caricamento delle informazioni…"), busy=True)

        def done(info):
            self._info = info
            self.infoChanged.emit()
            self._set(status="", busy=False)

        def fail(e):
            if isinstance(e, FarmError) and e.status == 403:      # chiave revocata: si ricomincia dall'abbinamento
                self.unpair()
            self._set(status=str(e), busy=False)

        self._run(lambda: self._client().info(), done, fail)

    @pyqtSlot()
    def openFarm(self):
        QDesktopServices.openUrl(QUrl(self.serverUrl))

    def _file_name(self):
        """Nome del lavoro + estensione e tipo: UFP se la stampante lo accetta (UltiMaker), altrimenti G-code."""
        app = CuraApplication.getInstance()
        stack = app.getGlobalContainerStack()
        formats = (stack.getMetaDataEntry("file_formats") or "") if stack else ""
        handler = self._file_handler or app.getMeshFileHandler()
        use_ufp = "application/x-ufp" in formats and handler.getWriterByMimeType("application/x-ufp") is not None
        job = app.getPrintInformation().jobName or "cura"
        return (job + (".ufp" if use_ufp else ".gcode")), ("application/x-ufp" if use_ufp else "text/x-gcode")

    def _write_file(self):
        """Scrive il file sezionato in un file temporaneo e ne restituisce il contenuto."""
        name, mime = self._file_name()
        handler = self._file_handler or CuraApplication.getInstance().getMeshFileHandler()
        writer = handler.getWriterByMimeType(mime)
        if writer is None:
            raise FarmError("writer %s non disponibile" % mime)
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(name)[1])
        os.close(fd)
        try:
            if mime == "text/x-gcode":
                with open(tmp, "w", encoding="utf-8") as fh:
                    ok = writer.write(fh, self._nodes, MeshWriter.OutputMode.TextMode)
            else:
                with open(tmp, "wb") as fh:
                    ok = writer.write(fh, self._nodes, MeshWriter.OutputMode.BinaryMode)
            if ok is False:
                raise FarmError(writer.getInformation() or tr("Niente da inviare: esegui prima lo slicing."))
            with open(tmp, "rb") as fh:
                return name, mime, fh.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @pyqtSlot(str, str, str, int, int, str)
    def send(self, project_id, target, action, copies, priority, notes):
        try:
            name, mime, data = self._write_file()            # nel thread dell'interfaccia: i writer di Cura lo richiedono
        except Exception as e:  # noqa: BLE001
            self._set(status="%s: %s" % (tr("Invio non riuscito"), e))
            return
        self._set_pref("last_project", project_id)
        self._set_pref("last_target", target)
        self._set_pref("last_action", action)
        self._set(status=tr("Invio in corso…"), busy=True)
        fields = {"project_id": project_id, "target": target, "action": action, "copies": str(max(1, copies)),
                  "priority": str(priority), "notes": notes}
        progress = Message(tr("Invio in corso…"), lifetime=0, dismissable=False, progress=-1,
                           title=tr("Invia a Fabbrix Farm Manager"))
        progress.show()
        self.writeStarted.emit(self)

        def done(res):
            progress.hide()
            self._set(status="", busy=False)
            f = res.get("filename") or name
            if res.get("duplicate") and not res.get("production_id"):
                text = tr("«{f}» era già nei Files.", f=f)
            elif res.get("production_id"):
                text = tr("«{f}» è nei Files ed è stato creato l'ordine di produzione.", f=f)
                if (self._info or {}).get("orders_paused"):
                    text += " " + tr("Gli ordini dagli slicer partono in pausa: avviali dalla Produzione.")
            else:
                text = tr("«{f}» è nei Files.", f=f)
            msg = Message(text, lifetime=15, title=tr("Inviato a Fabbrix Farm Manager"))
            msg.addAction("open", tr("Apri il Farm Manager"), "[no_icon]", "[no_description]")
            url = res.get("url") or self.serverUrl
            msg.actionTriggered.connect(lambda m, a: QDesktopServices.openUrl(QUrl(url)))
            msg.show()
            self.writeSuccess.emit(self)
            self.writeFinished.emit(self)
            if self._dialog is not None:
                self._dialog.close()

        def fail(e):
            progress.hide()
            self._set(status="%s: %s" % (tr("Invio non riuscito"), e), busy=False)
            self.writeError.emit(self)
            self.writeFinished.emit(self)

        self._run(lambda: self._client().upload(name, data, fields, mime), done, fail)
