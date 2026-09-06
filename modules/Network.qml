import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Networking
import "../" as K
import "../lib/parse.js" as Parse

K.Pill {
    id: root
    active: menu.open
    onClicked: menu.open = !menu.open

    readonly property var wifi:  Networking.devices.values.find(d => d.type === DeviceType.Wifi)  ?? null
    readonly property var wired: Networking.devices.values.find(d => d.type === DeviceType.Wired) ?? null
    readonly property var activeNet: wifi ? (wifi.networks.values.find(n => n.connected) ?? null) : null
    readonly property bool wiredUp: wired?.connected ?? false

    // Scanning only while the popup is open: it costs power and the list is
    // useless when nobody is looking at it.
    Connections { target: menu; function onOpenChanged() { if (root.wifi) root.wifi.scannerEnabled = menu.open } }

    // Network we are trying to join; its connectionFailed goes to the error line.
    property var pending: null
    property string error: ""
    Connections {
        target: root.pending
        function onConnectionFailed(reason) {
            root.error = (reason === ConnectionFailReason.NoSecrets || reason === ConnectionFailReason.WifiAuthTimeout)
                ? "wrong password" : ConnectionFailReason.toString(reason)
        }
        function onConnectedChanged() { if (root.pending?.connected) { root.pending = null; root.error = ""; pskField.text = "" } }
    }
    function join(n) {
        root.error = ""; root.pending = n
        if (n.known || n.security === WifiSecurityType.Open || n.security === WifiSecurityType.Owe) { n.connect(); return }
        if ([WifiSecurityType.Wpa2Psk, WifiSecurityType.WpaPsk, WifiSecurityType.Sae].includes(n.security)) { pskFor = n; pskField.forceActiveFocus(); return }
        root.error = "enterprise network — use Advanced"
    }
    property var pskFor: null

    Text {
        text: root.wiredUp ? "\u{F0200}" : Parse.wifiGlyph(root.activeNet?.signalStrength ?? null, root.activeNet !== null)
        color: (root.wiredUp || root.activeNet) ? K.Theme.text : K.Theme.dim
        font.pixelSize: 16; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
    }

    K.Popup {
        id: menu
        pill: root
        contentWidth: 300
        readonly property int colW: contentWidth - 32
        readonly property var list: {
            if (!root.wifi) return []
            var seen = {}, out = []
            root.wifi.networks.values.slice().sort((a, b) => (b.connected - a.connected) || (b.signalStrength - a.signalStrength))
                .forEach(n => { if (n.name && !seen[n.name]) { seen[n.name] = 1; out.push(n) } })
            return out
        }

        Row {
            width: menu.colW
            Text { text: "Wi-Fi"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; anchors.verticalCenter: parent.verticalCenter; width: menu.colW - 60 }
            Switch { checked: Networking.wifiEnabled; onToggled: Networking.wifiEnabled = checked }
        }
        Text { visible: root.wiredUp; text: "\u{F0200}  " + (root.wired?.name ?? "ethernet") + " connected"; color: K.Theme.dim; font.pixelSize: 12; font.family: K.Theme.iconFont }
        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }

        Repeater {
            model: menu.list
            Rectangle {
                width: menu.colW; height: 32; radius: 8
                color: ma.containsMouse ? "#33ffffff" : "transparent"
                Row {
                    anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                    spacing: 8
                    Text { text: Parse.wifiGlyph(modelData.signalStrength, true); color: K.Theme.text; font.pixelSize: 14; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: modelData.name; color: modelData.connected ? K.Theme.accent : K.Theme.text; font.pixelSize: K.Theme.fontPx; elide: Text.ElideRight; width: menu.colW - 90; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: modelData.connected ? "✓" : (modelData.security !== WifiSecurityType.Open ? "\u{F033E}" : ""); color: K.Theme.dim; font.pixelSize: 12; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
                }
                MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; onClicked: if (!modelData.connected) root.join(modelData) }
            }
        }

        Column {
            visible: root.pskFor !== null
            width: menu.colW; spacing: 6
            Text { text: "Password for " + (root.pskFor?.name ?? ""); color: K.Theme.dim; font.pixelSize: 12 }
            Row {
                spacing: 6
                TextField {
                    id: pskField
                    width: menu.colW - 70; echoMode: TextInput.Password
                    color: K.Theme.text; placeholderText: "••••••••"
                    background: Rectangle { radius: 8; color: "#1effffff"; border.color: K.Theme.faint }
                    onAccepted: { root.pskFor.connectWithPsk(text); root.pending = root.pskFor }
                }
                Button { text: "Join"; width: 64; onClicked: pskField.accepted() }
            }
        }
        Text { visible: root.error !== ""; text: root.error; color: K.Theme.bad; font.pixelSize: 12 }

        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }
        Text {
            text: "Advanced…"; color: K.Theme.dim; font.pixelSize: 12
            MouseArea { anchors.fill: parent; onClicked: { menu.open = false; Quickshell.execDetached(["nm-connection-editor"]) } }
        }
    }
    // Popups with text input need keyboard focus; PopupWindow takes it via grabFocus.
    Component.onCompleted: menu.grabFocus = true
}
