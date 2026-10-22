import QtQuick
import Quickshell
import Quickshell.Io
import "../" as K
import "../lib/parse.js" as Parse

// Screen recording: wf-recorder started by ~/.local/bin/screenrec.sh (Ctrl+Shift+4
// area, Ctrl+Shift+3 full screen). The script owns the truth: `screenrec.sh status`
// prints "<pid> <seconds> <on|off> [x y w h]" while it records. The pill asks on
// load (so a reload keeps the right time) and on the script's `screenrec refresh`
// IPC, then blocks on the recorder's pid with waitpid (a pidfd, no polling) and
// hides when it exits, whoever stopped it. Left click stops, right click toggles
// the mic. An area recording also gets ScreenRecFrame: the rest of the screen
// dimmed and the same controls floating just outside the area.
// Idle cost: no process, no timer.
K.Pill {
    id: root
    property int pid: 0                 // wf-recorder; 0 = not recording
    property real since: 0              // epoch seconds the recording started
    property bool mic: false
    property var area: null             // {x, y, w, h} in layout coordinates; null = full screen
    property real now: Date.now() / 1000
    property bool blink: false
    readonly property bool stopping: stopGuard.running
    readonly property string script: Quickshell.env("HOME") + "/.local/bin/screenrec.sh"

    visible: pid > 0
    edge: stopping ? K.Theme.faint : K.Theme.bad
    onPidChanged: if (pid === 0) { stopGuard.stop(); area = null }

    onClicked: button => { if (button === Qt.RightButton) toggleMic(); else if (button === Qt.LeftButton) stop() }
    function stop() {
        if (stopping) return
        // Without the guard a second click after wf-recorder exits would run the
        // toggle again and start a new recording.
        stopGuard.restart()
        Quickshell.execDetached([root.script])
    }
    function toggleMic() {
        if (stopping || micCmd.running) return
        mic = !mic                                         // optimistic; the refresh after the verb confirms
        micCmd.running = true
    }
    function refresh() { if (probe.running) probe.again = true; else probe.running = true }   // IPC `screenrec refresh`

    Text {
        text: "\u{F0567}"                                  // video camera: tells it apart from the call recorder's dot
        color: root.stopping ? K.Theme.dim : K.Theme.bad
        // Blinks on the 1 Hz tick, like Rec.qml: an infinite animation would
        // re-render the bar at vsync for the whole recording.
        opacity: root.blink ? 0.35 : 1
        Behavior on opacity { NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } }
        font.pixelSize: 16; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        id: label
        text: Parse.fmtClock(root.now - root.since)
        // Pinned to the all-zeros width so the pill does not twitch every second.
        width: Math.max(implicitWidth, pin.width)
        color: root.stopping ? K.Theme.dim : K.Theme.text
        font.pixelSize: K.Theme.fontPx
        anchors.verticalCenter: parent.verticalCenter
    }
    TextMetrics { id: pin; font: label.font; text: label.text.replace(/\d/g, "0") }
    Text {
        text: root.mic ? "\u{F036C}" : "\u{F036D}"
        color: root.mic && !root.stopping ? K.Theme.text : K.Theme.dim
        font.pixelSize: 16; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
    }

    Timer {
        interval: 1000; repeat: true; triggeredOnStart: true
        running: root.pid > 0 && (!K.Kozy.fsActive || frame.visible)   // the frame shows over fullscreen too
        onTriggered: { root.now = Date.now() / 1000; root.blink = !root.stopping && !root.blink }
        onRunningChanged: if (!running) root.blink = false
    }
    Timer { id: stopGuard; interval: 5000 }               // cleared when the recorder exits; 5 s if it never does

    Process {
        id: probe
        property bool again: false
        command: [root.script, "status"]
        stdout: StdioCollector {
            onStreamFinished: {
                var s = Parse.screenrecStatus(text)
                if (!s) { root.pid = 0; return }
                // Same recording (a mic toggle, a repeated IPC): keep the start time,
                // `ps etimes` is whole seconds and would jitter the clock.
                if (s.pid !== root.pid) root.since = Date.now() / 1000 - s.secs
                root.now = Date.now() / 1000
                root.mic = s.mic
                root.area = s.area
                root.pid = s.pid
                root.follow(s.pid)
            }
        }
        onExited: if (again) { again = false; running = true }
    }

    // waitpid exits 0 when the recorder exits, non-zero when it could not wait
    // (the pid was already gone): then re-ask after a pause instead of spinning.
    Process {
        id: watch
        property int target: 0
        command: ["waitpid", String(target)]
        onExited: code => { target = 0; if (code === 0) root.refresh(); else retry.start() }
    }
    Timer { id: retry; interval: 3000; onTriggered: root.refresh() }
    // A watch still running for an older pid ends on its own and re-asks.
    function follow(p) { if (!watch.running) { watch.target = p; watch.running = true } }

    Process {
        id: micCmd
        command: [root.script, "mic"]
        onExited: root.refresh()
    }

    ScreenRecFrame { id: frame; rec: root }

    Component.onCompleted: refresh()                      // a reload mid-recording picks the pill back up
}
