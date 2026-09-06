import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Bluetooth
import "../" as K

K.Pill {
    id: root
    active: menu.open
    onClicked: menu.open = !menu.open

    readonly property var adapter: Bluetooth.defaultAdapter
    readonly property bool on: adapter?.enabled ?? false
    readonly property var connectedDev: adapter ? (adapter.devices.values.find(d => d.connected) ?? null) : null
    visible: adapter !== null

    Text {
        text: !root.on ? "\u{F00B2}" : root.connectedDev ? "\u{F00B1}" : "\u{F00AF}"
        color: root.on ? K.Theme.text : K.Theme.dim
        font.pixelSize: 16; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
    }
    // Name of the connected device in the bar, like HyprPanel's bluetooth label.
    Text {
        visible: root.connectedDev !== null
        text: root.connectedDev?.name ?? ""
        color: K.Theme.text; font.pixelSize: K.Theme.fontPx
        elide: Text.ElideRight; width: Math.min(implicitWidth, 140)
        anchors.verticalCenter: parent.verticalCenter
    }

    Timer { id: scanStop; interval: 10000; onTriggered: if (root.adapter) root.adapter.discovering = false }

    K.Popup {
        id: menu
        pill: root
        contentWidth: 300
        readonly property int colW: contentWidth - 32
        onOpenChanged: if (!open && root.adapter) root.adapter.discovering = false
        readonly property var list: root.adapter
            ? root.adapter.devices.values.filter(d => d.name !== "" && (d.paired || d.connected || root.adapter.discovering))
                  .slice().sort((a, b) => (b.connected - a.connected) || (b.paired - a.paired))
            : []

        Row {
            width: menu.colW
            Text { text: "Bluetooth"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; anchors.verticalCenter: parent.verticalCenter; width: menu.colW - 60 }
            Switch { checked: root.on; onToggled: root.adapter.enabled = checked }
        }
        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }

        Repeater {
            model: menu.list
            Rectangle {
                width: menu.colW; height: 32; radius: 8
                color: ma.containsMouse ? "#33ffffff" : "transparent"
                Row {
                    anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                    spacing: 8
                    Text { text: modelData.name; color: modelData.connected ? K.Theme.accent : K.Theme.text; font.pixelSize: K.Theme.fontPx; elide: Text.ElideRight; width: menu.colW - 110; anchors.verticalCenter: parent.verticalCenter }
                    Text { visible: modelData.batteryAvailable; text: Math.round(modelData.battery * 100) + "%"; color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: modelData.state === BluetoothDeviceState.Connecting ? "…" : modelData.connected ? "✓" : modelData.paired ? "" : "new"
                        color: K.Theme.dim; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
                    }
                }
                MouseArea {
                    id: ma; anchors.fill: parent; hoverEnabled: true
                    onClicked: {
                        if (modelData.connected) modelData.disconnect()
                        else if (modelData.paired) modelData.connect()
                        else modelData.pair()   // bluez auto-connects most devices after pairing
                    }
                }
            }
        }
        Text { visible: menu.list.length === 0; text: root.on ? "no paired devices — Scan" : "adapter off"; color: K.Theme.dim; font.pixelSize: 12 }

        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }
        Text {
            text: root.adapter?.discovering ? "Scanning…" : "Scan"; color: K.Theme.dim; font.pixelSize: 12
            MouseArea { anchors.fill: parent; enabled: root.on && !(root.adapter?.discovering ?? true)
                onClicked: { root.adapter.discovering = true; scanStop.restart() } }
        }
    }
}
