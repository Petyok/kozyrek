import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Services.Pipewire
import "lib/parse.js" as Parse

// Volume / screen brightness / keyboard brightness OSD. Driven only by IPC:
// `qs -c kozyrek ipc call osd volume +5`. qs applies the change itself and
// shows the result, so there is nothing to poll.
Scope {
    id: osd
    property string kind: ""       // "vol" | "bri" | "kbd"
    property real   level: 0       // 0..1.5 for volume, 0..1 for brightness
    property bool   muted: false
    property bool   shown: false
    Timer { id: hide; interval: 1500; onTriggered: osd.shown = false }
    function show(k, lvl, m) { kind = k; level = lvl; muted = m === true; shown = true; hide.restart() }

    // Without a tracker the default sink's audio properties never populate.
    PwObjectTracker { objects: [Pipewire.defaultAudioSink] }

    Process {
        id: bri
        property string kind: "bri"
        stdout: StdioCollector { onStreamFinished: { var f = Parse.brightnessctl(text); if (!isNaN(f)) osd.show(bri.kind, f) } }
    }
    function setBrightness(kind, device, arg) {
        bri.kind = kind
        bri.command = ["brightnessctl", "-m", "-d", device, "set", Parse.brightnessArg(arg)]
        bri.running = true
    }

    IpcHandler {
        target: "osd"
        function volume(arg: string): void {
            var a = Pipewire.defaultAudioSink?.audio
            if (!a) return
            if (arg === "mute") a.muted = !a.muted
            else a.volume = Parse.stepVolume(a.volume, arg, 1.5)   // mute is only ever toggled by the mute key
            osd.show("vol", a.volume, a.muted)
        }
        function brightness(arg: string): void { osd.setBrightness("bri", "intel_backlight", arg) }
        function kbd(arg: string): void        { osd.setBrightness("kbd", "smc::kbd_backlight", arg) }
    }

    PanelWindow {
        visible: osd.shown   // Overlay layer: shown over fullscreen too, like swayosd did
        anchors { bottom: true }
        margins { bottom: 120 }
        implicitWidth: 280
        implicitHeight: 56
        color: "transparent"
        WlrLayershell.namespace: "kozyrek-osd"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore

        Rectangle {
            anchors.fill: parent
            radius: 16; color: Theme.bg; border.color: Theme.faint; border.width: 1
            Row {
                anchors { fill: parent; margins: 14 }
                spacing: 12
                Text {
                    text: osd.kind === "vol" ? (osd.muted ? "\u{F0581}" : "\u{F057E}") : osd.kind === "bri" ? "\u{F00E0}" : "\u{F030C}"
                    color: Theme.text; font.pixelSize: 20; font.family: Theme.iconFont
                    anchors.verticalCenter: parent.verticalCenter
                }
                Rectangle {
                    width: parent.width - 90; height: 6; radius: 3
                    color: Theme.faint
                    anchors.verticalCenter: parent.verticalCenter
                    Rectangle {
                        width: parent.width * Math.min(1, osd.level / (osd.kind === "vol" ? 1.5 : 1))
                        height: parent.height; radius: 3
                        color: osd.muted ? Theme.dim : (osd.kind === "vol" && osd.level > 1) ? Theme.warn : Theme.accent
                        Behavior on width { NumberAnimation { duration: 80 } }
                    }
                }
                Text {
                    text: Math.round(osd.level * 100) + "%"
                    color: Theme.text; font.pixelSize: Theme.fontPx; font.bold: true
                    width: 40; horizontalAlignment: Text.AlignRight
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
}
