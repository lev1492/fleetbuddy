// Fabbrix Farm Manager – finestra di abbinamento e di invio (Cura 5, Qt 6).
// "manager" è FabbrixOutputDevice (Python): testi con manager.t(), dati in manager.info.
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

Window {
    id: win
    title: "Fabbrix Farm Manager"
    width: 480
    height: 600
    minimumWidth: 420
    minimumHeight: 420
    modality: Qt.ApplicationModal
    flags: Qt.Dialog
    color: sysPal.window

    SystemPalette { id: sysPal; colorGroup: SystemPalette.Active }

    readonly property var info: manager.info
    readonly property bool hasInfo: info && info.projects !== undefined
    readonly property var perms: hasInfo ? info.perms : ({})

    function projectModel() {
        var out = [];
        if (!hasInfo) return out;
        for (var i = 0; i < info.projects.length; i++) {
            var p = info.projects[i];
            out.push({value: p.id === null || p.id === undefined ? "none" : String(p.id), text: p.path});
        }
        return out;
    }
    function targetModel() {
        var out = [{value: "", text: manager.t("Tutte le stampanti compatibili")}];
        if (!hasInfo) return out;
        for (var i = 0; i < info.groups.length; i++)
            out.push({value: "g:" + info.groups[i].id, text: manager.t("Gruppo {g}").replace("{g}", info.groups[i].name)});
        for (var j = 0; j < info.printers.length; j++) {
            var pr = info.printers[j];
            out.push({value: "p:" + pr.sn, text: pr.name + (pr.model ? " (" + pr.model + ")" : "")});
        }
        return out;
    }
    function indexOf(model, value, fallback) {
        for (var i = 0; i < model.length; i++) if (model[i].value === value) return i;
        return fallback;
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        Label { text: manager.paired ? manager.t("Invia a Fabbrix Farm Manager") : manager.t("Collega Cura al Farm Manager")
                font.bold: true; font.pointSize: 13 }

        // ---------------------------------------------------------------- abbinamento
        ColumnLayout {
            visible: !manager.paired
            Layout.fillWidth: true
            spacing: 8
            Label { text: manager.t("Indirizzo del Farm Manager") }
            TextField { id: urlField; Layout.fillWidth: true; text: manager.serverUrl
                        placeholderText: "http://192.168.1.20:8000"; enabled: manager.pairCode === "" }
            CheckBox { id: insecureBox; text: manager.t("Accetta certificati HTTPS autofirmati"); checked: manager.insecure
                       enabled: manager.pairCode === "" }
            ColumnLayout {
                visible: manager.pairCode !== ""
                Layout.fillWidth: true
                Label { text: manager.t("Approva nel Farm Manager il codice") }
                Label { text: manager.pairCode; font.pointSize: 24; font.bold: true; font.letterSpacing: 4 }
                Label { Layout.fillWidth: true; wrapMode: Text.Wrap
                        text: manager.t("Si è aperto il browser: entra nel Farm Manager e approva la richiesta. Se non si è aperto, vai su") + " " + manager.pairUrl }
                TextField { Layout.fillWidth: true; readOnly: true; text: manager.pairUrl; selectByMouse: true }
            }
        }

        // ---------------------------------------------------------------- invio
        GridLayout {
            visible: manager.paired && hasInfo
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 10
            rowSpacing: 8

            Label { text: manager.t("File") }
            Label { text: manager.fileName; Layout.fillWidth: true; elide: Text.ElideMiddle }
            Label { text: manager.t("Stampante in Cura") }
            Label { text: manager.machineName; Layout.fillWidth: true; elide: Text.ElideRight }

            Label { text: manager.t("Cartella dei Files") }
            ComboBox {
                id: projectBox
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: win.projectModel()
                onModelChanged: currentIndex = win.indexOf(model, manager.lastProject,
                    win.indexOf(model, hasInfo && info.default_project !== null && info.default_project !== undefined ? String(info.default_project) : "", 0))
            }

            Label { text: manager.t("Destinazione") }
            ComboBox {
                id: targetBox
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: win.targetModel()
                onModelChanged: currentIndex = win.indexOf(model, manager.lastTarget, 0)
            }

            Label { text: manager.t("Azione") }
            ComboBox {
                id: actionBox
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: perms.order ? [{value: "upload", text: manager.t("Solo carica nei Files")},
                                      {value: "order", text: manager.t("Carica e crea un ordine di produzione")}]
                                   : [{value: "upload", text: manager.t("Solo carica nei Files")}]
                onModelChanged: currentIndex = win.indexOf(model, manager.lastAction, 0)
            }

            Label { text: manager.t("Copie"); visible: actionBox.currentValue === "order" }
            SpinBox { id: copiesBox; from: 1; to: 999; value: 1; editable: true; visible: actionBox.currentValue === "order" }

            Label { text: manager.t("Priorità"); visible: actionBox.currentValue === "order" && perms.prioritize === true }
            ComboBox {
                id: prioBox
                visible: actionBox.currentValue === "order" && perms.prioritize === true
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: [{value: 0, text: manager.t("Bassa")}, {value: 1, text: manager.t("Normale")},
                        {value: 2, text: manager.t("Alta")}, {value: 3, text: manager.t("Urgente")}]
                currentIndex: 1
            }

            Label { text: manager.t("Note"); Layout.alignment: Qt.AlignTop }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 80
                TextArea { id: notesBox; wrapMode: TextEdit.Wrap }
            }
        }

        Item { Layout.fillHeight: true }

        Label { visible: manager.paired && manager.userName !== ""; color: sysPal.mid
                text: manager.t("Collegato come {u}").replace("{u}", manager.userName) + " · " + manager.serverUrl
                Layout.fillWidth: true; elide: Text.ElideRight }
        Label { text: manager.status; visible: manager.status !== ""; wrapMode: Text.Wrap; Layout.fillWidth: true }
        BusyIndicator { running: manager.busy; visible: manager.busy; Layout.alignment: Qt.AlignHCenter }

        RowLayout {
            Layout.fillWidth: true
            Button { text: manager.t("Scollega"); visible: manager.paired; onClicked: manager.unpair() }
            Item { Layout.fillWidth: true }
            Button { text: manager.t("Chiudi"); onClicked: manager.closeDialog() }
            Button {
                visible: !manager.paired
                text: manager.t("Collega")
                enabled: !manager.busy && manager.pairCode === "" && urlField.text.length > 0
                onClicked: manager.startPairing(urlField.text, insecureBox.checked)
            }
            Button {
                visible: manager.paired
                highlighted: true
                text: manager.t("Invia")
                enabled: !manager.busy && hasInfo && projectBox.currentIndex >= 0
                onClicked: manager.send(projectBox.currentValue, targetBox.currentValue, actionBox.currentValue,
                                        copiesBox.value, prioBox.currentValue === undefined ? 1 : prioBox.currentValue,
                                        notesBox.text)
            }
        }
    }

    onClosing: manager.cancelPairing()
}
