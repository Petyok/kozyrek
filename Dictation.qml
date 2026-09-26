import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "lib/parse.js" as Parse

// Dictation, macOS style: tap right Cmd, speak Russian or English, the words
// are typed into the focused window as they are recognized; tap again to stop
// (or just stop talking for 10 s). dict/kozydict.py does the listening and the
// typing; this draws the capsule and forwards the hotkey.
//
// The helper is a child of the bar on purpose, unlike the call recorder: a
// dictation is seconds long, and a reload that kills it loses one phrase. It
// lives only while a dictation does, so the ~1 GB model never stays resident.
// Idle cost: no process, no timer, no window.
Scope {
    id: root
    property string st: "idle"          // idle | listening | finishing | error
    property string text: ""            // everything typed so far
    property string note: ""            // a warning while listening, the message on error
    readonly property int nBars: 22
    property var bars: zeros()          // recent mic levels, oldest first
    readonly property bool open: st !== "idle"
    property bool mapped: false         // outlives `open` by the exit animation

    function zeros() { var a = []; for (var i = 0; i < nBars; i++) a.push(0); return a }

    function toggle() {                 // IPC `dict toggle`
        if (st === "listening") stop()
        else if (st === "idle" || st === "error") start()
    }
    function start() {
        errHide.stop()
        text = ""; note = ""; bars = zeros()
        st = "listening"
        engine.running = true
    }
    function stop() {
        st = "finishing"
        bars = zeros()
        engine.write("stop\n")
    }

    onOpenChanged: { if (open) { unmap.stop(); mapped = true } else unmap.restart() }
    Timer { id: unmap; interval: 240; onTriggered: root.mapped = false }
    Timer { id: errHide; interval: 3500; onTriggered: if (root.st === "error") root.st = "idle" }

    Process {
        id: engine
        property string err: ""
        command: ["python3", Quickshell.shellDir + "/dict/kozydict.py"]
        stdinEnabled: true
        stdout: SplitParser { onRead: line => root.feed(line) }
        stderr: SplitParser { onRead: line => { if (!line.startsWith("[asr]")) { engine.err = line; console.log("kozydict:", line) } } }
        onRunningChanged: if (running) err = ""
        onExited: (code, status) => {
            if (code === 0 && root.st !== "error") { root.st = "idle"; return }
            if (root.st !== "error") root.fail(engine.err || ("kozydict exited with " + code))
        }
    }

    function feed(line) {
        var e = Parse.recParse(line)
        if (!e) { console.log("dict: bad line:", line); return }
        switch (e.ev) {
        case "level":
            if (st === "listening") bars = bars.slice(1).concat([Math.max(0, Math.min(1, Number(e.v) || 0))])
            break
        case "text":  text = String(e.text || ""); note = ""; break
        case "warn":  note = String(e.msg || ""); break
        case "state": if (e.state === "finishing" && st === "listening") { st = "finishing"; bars = zeros() } break
        case "error": fail(String(e.msg || "dictation failed")); break
        }
    }
    function fail(msg) {
        note = msg; st = "error"; errHide.restart()
        // The capsule shows the error for 3.5 s, elided; the notification keeps the whole message.
        Quickshell.execDetached(["notify-send", "-a", "kozyrek", "Dictation failed", msg])
    }

    PanelWindow {
        visible: root.mapped             // Overlay layer: over fullscreen windows too
        anchors { bottom: true }
        margins { bottom: 34 }
        implicitWidth: 660
        implicitHeight: 80
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        mask: Region { item: capsule }   // the transparent rest of the window stays click-through
        WlrLayershell.namespace: "kozyrek-dict"
        WlrLayershell.layer: WlrLayer.Overlay
        // Never take the keyboard: the typing must land in the window you were in.
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

        Item {
            id: body
            width: capsule.width; height: capsule.height
            anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 12 }
            opacity: root.open ? 1 : 0
            scale: root.open ? 1 : 0.86
            transform: Translate { y: root.open ? 0 : 12; Behavior on y { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } } }
            Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }

            // Soft shadow: three widening translucent capsules, cheaper than a blur pass.
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    anchors { centerIn: capsule; verticalCenterOffset: 4 }
                    width: capsule.width + (index + 1) * 6; height: capsule.height + (index + 1) * 6
                    radius: height / 2
                    color: Qt.rgba(0, 0, 0, 0.13 - index * 0.04)
                }
            }

            // Frosted glass: Hyprland blurs what is behind (layer rule on kozyrek-dict).
            Rectangle {
                id: capsule
                height: 52
                width: row.implicitWidth + 30
                radius: height / 2
                color: "#b8141826"
                border.width: 1
                border.color: "#2effffff"
                Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

                Rectangle {                           // top sheen
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
                    height: parent.height / 2; radius: parent.radius - 1
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#14ffffff" }
                        GradientStop { position: 1; color: "#00ffffff" }
                    }
                }

                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.toggle() }

                Row {
                    id: row
                    anchors { left: parent.left; leftMargin: 9; verticalCenter: parent.verticalCenter }
                    spacing: 14

                    // Mic orb; its halo breathes with the voice.
                    Item {
                        width: 36; height: 36
                        anchors.verticalCenter: parent.verticalCenter
                        readonly property real lvl: root.st === "listening" ? root.bars[root.nBars - 1] : 0
                        Rectangle {
                            anchors.centerIn: parent
                            width: 36; height: 36; radius: 18
                            color: root.st === "error" ? Theme.bad : Theme.accent
                            opacity: 0.35
                            scale: 1 + Math.sqrt(parent.lvl) * 0.45
                            Behavior on scale { NumberAnimation { duration: 90 } }
                        }
                        Rectangle {
                            id: orb
                            anchors.centerIn: parent
                            width: 36; height: 36; radius: 18
                            color: root.st === "error" ? Theme.bad : Theme.accent
                            Behavior on color { ColorAnimation { duration: 150 } }
                            SequentialAnimation on opacity {         // "working on the last words"
                                running: root.st === "finishing"
                                loops: Animation.Infinite
                                NumberAnimation { to: 0.45; duration: 450; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 1; duration: 450; easing.type: Easing.InOutSine }
                                onRunningChanged: if (!running) orb.opacity = 1
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            text: root.st === "error" ? "\u{F036D}" : "\u{F036C}"
                            color: "white"; font.pixelSize: 19; font.family: Theme.iconFont
                        }
                    }

                    // Live waveform: the last 1.1 s of mic level, newest on the right.
                    Row {
                        visible: root.st !== "error"
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3
                        Repeater {
                            model: root.nBars
                            Rectangle {
                                required property int index
                                width: 3; radius: 1.5
                                height: 4 + 24 * Math.sqrt(root.bars[index])    // sqrt: a quiet voice still moves the bars
                                anchors.verticalCenter: parent.verticalCenter
                                color: "white"
                                opacity: root.st === "listening" ? 0.35 + 0.65 * (index + 1) / root.nBars : 0.3
                                Behavior on height { NumberAnimation { duration: 80; easing.type: Easing.OutQuad } }
                            }
                        }
                    }

                    Text {
                        id: words
                        readonly property bool placeholder: root.text === ""
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.min(implicitWidth, 400)
                        text: root.st === "error" || (placeholder && root.note) ? root.note
                            : placeholder ? (root.st === "finishing" ? "Finishing…" : "Listening…") : root.text
                        elide: root.st === "error" ? Text.ElideRight : Text.ElideLeft   // the newest words stay in view
                        color: root.st === "error" ? Theme.bad : (placeholder && root.note) ? Theme.warn
                             : placeholder ? Theme.dim : Theme.text
                        font.pixelSize: 15
                    }

                    Rectangle {                              // which language the model is hearing
                        readonly property string lang: Parse.dictLang(root.text)
                        visible: lang !== "" && root.st !== "error"
                        anchors.verticalCenter: parent.verticalCenter
                        width: 28; height: 20; radius: 6
                        color: Theme.faint
                        Text {
                            anchors.centerIn: parent
                            text: parent.lang
                            color: Theme.text; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.5
                        }
                    }
                }
            }
        }
    }
}
