import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Services.UPower
import "../" as K
import "../lib/parse.js" as Parse

K.Pill {
    id: root
    active: menu.open
    onClicked: menu.open = !menu.open

    readonly property var dev: UPower.displayDevice
    // UPowerDevice.percentage is a 0..1 fraction in Quickshell.
    readonly property int pct: Math.round((dev?.percentage ?? 0) * 100)
    readonly property bool charging: dev?.state === UPowerDeviceState.Charging || dev?.state === UPowerDeviceState.FullyCharged
    readonly property color tone: charging ? K.Theme.text : pct <= 5 ? K.Theme.bad : pct <= 15 ? K.Theme.warn : K.Theme.text
    visible: dev?.isLaptopBattery ?? false

    function hm(sec) { if (!sec || sec <= 0) return "—"; var h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60); return h + "h " + ("0" + m).slice(-2) + "m" }

    Text { text: Parse.batteryGlyph(root.pct, root.charging); color: root.tone; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
    Text { text: root.pct + "%"; color: root.tone; font.pixelSize: K.Theme.fontPx; anchors.verticalCenter: parent.verticalCenter }

    // One warning per crossing below 15 % while discharging; re-armed by charging.
    property bool warned: false
    onPctChanged: {
        if (!charging && pct <= 15 && !warned) {
            warned = true
            Quickshell.execDetached(["notify-send", "-a", "kozyrek", "-u", "critical", "Battery low", pct + "% left"])
        }
    }
    onChargingChanged: if (charging) warned = false

    // power-profiles-daemon: read on open, write on click, re-read after write.
    property string profile: ""
    Process {
        id: ppdGet
        command: ["powerprofilesctl", "get"]
        stdout: StdioCollector { onStreamFinished: root.profile = text.trim() }
    }
    Process {
        id: ppdSet
        property string target: ""
        command: ["powerprofilesctl", "set", target]
        onExited: ppdGet.running = true
    }
    function setProfile(p) { ppdSet.target = p; ppdSet.running = true }

    K.Popup {
        id: menu
        pill: root
        contentWidth: 260
        readonly property int colW: contentWidth - 32
        onOpenChanged: if (open) ppdGet.running = true

        Row {
            spacing: 10
            Text { text: Parse.batteryGlyph(root.pct, root.charging); color: root.tone; font.pixelSize: 28; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
            Column {
                spacing: -1
                Text { text: root.pct + "%"; color: K.Theme.text; font.pixelSize: 24; font.bold: true }
                Text { text: UPowerDeviceState.toString(root.dev?.state ?? 0); color: K.Theme.dim; font.pixelSize: 12 }
            }
        }
        Grid {
            columns: 2; columnSpacing: 12; rowSpacing: 2
            Text { text: root.charging ? "to full" : "to empty"; color: K.Theme.dim; font.pixelSize: 12 }
            Text { text: root.hm(root.charging ? root.dev?.timeToFull : root.dev?.timeToEmpty); color: K.Theme.text; font.pixelSize: 12 }
            Text { text: "rate"; color: K.Theme.dim; font.pixelSize: 12 }
            Text { text: (root.dev?.changeRate ?? 0).toFixed(1) + " W"; color: K.Theme.text; font.pixelSize: 12 }
            Text { text: "health"; color: K.Theme.dim; font.pixelSize: 12; visible: root.dev?.healthSupported ?? false }
            Text { text: Math.round(root.dev?.healthPercentage ?? 0) + "%"; color: K.Theme.text; font.pixelSize: 12; visible: root.dev?.healthSupported ?? false }
        }
        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }
        Text { text: "Power profile"; color: K.Theme.dim; font.pixelSize: 11 }
        Repeater {
            model: [
                { id: "performance", t: "Performance", i: "\u{F0210}" },
                { id: "balanced",    t: "Balanced",    i: "\u{F0140}" },
                { id: "power-saver", t: "Power saver", i: "\u{F0335}" },
            ]
            Rectangle {
                width: menu.colW; height: 32; radius: 8
                readonly property bool cur: root.profile === modelData.id
                color: cur ? K.Theme.accent : (ma.containsMouse ? "#33ffffff" : "transparent")
                Row {
                    anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                    spacing: 10
                    Text { text: modelData.i; color: cur ? "white" : K.Theme.text; font.pixelSize: 15; font.family: K.Theme.iconFont }
                    Text { text: modelData.t; color: cur ? "white" : K.Theme.text; font.pixelSize: K.Theme.fontPx }
                }
                MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; onClicked: root.setProfile(modelData.id) }
            }
        }
    }
}
