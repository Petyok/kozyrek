import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import "../" as K

// Active xkb layout as a two-letter chip ("EN", "RU"). Seeded from hyprctl
// (the main keyboard), kept live by the `activelayout` event; click cycles it.
K.Pill {
    id: root
    property string layout: ""
    function short(name) { return name.trim().split(" ")[0].slice(0, 2).toUpperCase() }
    visible: layout.length > 0

    Process {
        command: ["hyprctl", "-j", "devices"]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try { root.layout = root.short(JSON.parse(text).keyboards.find(k => k.main).active_keymap) }
                catch (e) { console.log("layout: hyprctl devices:", e) }
            }
        }
    }
    Connections {
        target: Hyprland
        function onRawEvent(event) {
            if (event.name !== "activelayout") return
            var p = event.data.split(",")
            root.layout = root.short(p[p.length - 1])
        }
    }

    Text { text: root.layout; color: root.layout === "EN" ? K.Theme.dim : K.Theme.accent; font.pixelSize: K.Theme.fontPx; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
    onClicked: Quickshell.execDetached(["hyprctl", "switchxkblayout", "all", "next"])
}
