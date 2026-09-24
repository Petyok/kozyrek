import QtQuick
import Quickshell
import Quickshell.Io
import "../" as K
import "../lib/parse.js" as Parse

// Call recorder → transcript. rec/kozyrec.py owns the truth: the recorder and the
// transcriber run detached (systemd --user units / setsid), so a bar reload loses
// nothing and this pill only draws the helper's state and forwards clicks.
// Quickshell SIGKILLs Process children on reload, hence the only long-lived child
// here is the read-only follower, and only while something is running.
// Idle cost: no process, no timer.
K.Pill {
    id: root
    property var m: Parse.recInit()
    readonly property string st: m.state
    property real now: Date.now() / 1000
    property bool blink: false
    readonly property string script: Quickshell.shellDir + "/rec/kozyrec.py"
    readonly property string callsDir: Quickshell.env("HOME") + "/Calls"

    active: menu.open
    progress: st === "transcribing" ? Math.max(0, m.frac) : -1
    // A live warning (mic muted, no call audio…) turns the recording border amber
    // rather than adding a glyph: the right zone has little room next to the clock.
    edge: st === "recording" ? (m.warn ? K.Theme.warn : K.Theme.bad) : st === "error" ? K.Theme.bad
        : (st === "ask" || st === "done") ? K.Theme.accent : K.Theme.faint

    onClicked: button => act(Parse.recClick(st, button === Qt.RightButton ? "right" : button === Qt.MiddleButton ? "middle" : "left"))
    function toggle() { act(Parse.recToggle(st)) }        // IPC `rec toggle`

    // ── pill content ────────────────────────────────────────
    Text {
        visible: text !== ""
        text: Parse.recGlyph(root.st)                      // "" while recording/stopping: the dot takes over
        color: root.active ? "white" : root.st === "idle" ? K.Theme.dim : root.st === "error" ? K.Theme.bad
             : (root.st === "ask" || root.st === "done") ? K.Theme.accent : K.Theme.text
        font.pixelSize: 16; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
    }
    Rectangle {
        visible: root.st === "recording" || root.st === "stopping"
        width: 10; height: 10; radius: 5
        color: root.st === "recording" ? K.Theme.bad : K.Theme.dim
        // Blinks on the 1 Hz tick: an infinite animation would re-render the whole
        // bar at vsync for the entire call.
        opacity: root.blink ? 0.35 : 1
        Behavior on opacity { NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } }
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        id: label
        visible: text !== ""
        text: Parse.recPillText(root.m, root.now)
        // Pinned to the all-zeros width of the same string so the pill does not
        // twitch every second.
        width: Math.max(implicitWidth, pin.width)
        color: root.active ? "white" : root.st === "stopping" ? K.Theme.dim : root.st === "ask" ? K.Theme.accent : K.Theme.text
        font.pixelSize: K.Theme.fontPx
        font.bold: root.st === "transcribing" || root.st === "ask"
        anchors.verticalCenter: parent.verticalCenter
    }
    TextMetrics { id: pin; font: label.font; text: label.text.replace(/\d/g, "0") }

    // 1 Hz, only while something on screen changes with time.
    Timer {
        interval: 1000; repeat: true; triggeredOnStart: true
        running: !K.Kozy.fsActive && (root.st === "recording" || (menu.open && root.st === "transcribing"))
        onTriggered: { root.now = Date.now() / 1000; root.blink = root.st === "recording" && !root.blink }
        onRunningChanged: if (!running) root.blink = false
    }
    Timer { id: guard; interval: 600 }                    // double-click / hotkey+click debounce

    // ── helper ──────────────────────────────────────────────
    // Short mutating verbs, one at a time; their lines go through the same reducer.
    Process {
        id: cmd
        property bool heard: false
        property string lastErr: ""
        stdout: SplitParser { onRead: line => { cmd.heard = true; root.feed(line) } }
        stderr: SplitParser { onRead: line => { cmd.lastErr = line; console.log("kozyrec:", line) } }
        onExited: (code, status) => {
            watchdog.stop()
            if (code !== 0 && !cmd.heard) root.apply({ ev: "error", msg: cmd.lastErr || ("kozyrec exited with " + code) })
            root.attach()                                  // the follower is the truth from here on
        }
    }
    Timer { id: watchdog; interval: 15000; onTriggered: { cmd.lastErr = "kozyrec did not answer in 15 s"; cmd.signal(9) } }
    function run(args) {
        if (cmd.running) return false
        cmd.heard = false; cmd.lastErr = ""
        cmd.command = ["python3", root.script].concat(args)
        cmd.running = true
        watchdog.restart()
        return true
    }

    // Read-only follower: prints the state now, streams changes, exits by itself on
    // a stable state (idle/ask/done/error). Dying while something is live means it
    // crashed: re-attach, give up after three silent deaths.
    Process {
        id: follow
        property int fails: 0
        command: ["python3", root.script, "status", "--follow"]
        stdout: SplitParser { onRead: line => { follow.fails = 0; root.feed(line) } }
        stderr: SplitParser { onRead: line => console.log("kozyrec follow:", line) }
        onExited: {
            if (!Parse.recLive(root.st)) return
            if (++follow.fails > 3) root.apply({ ev: "error", msg: "lost contact with kozyrec" })
            else reattach.start()
        }
    }
    Timer { id: reattach; interval: 3000; onTriggered: root.attach() }
    function attach() { if (!follow.running) follow.running = true }
    Component.onCompleted: attach()                       // re-attach after every reload

    function feed(line) {
        var e = Parse.recParse(line)
        if (e) apply(e); else console.log("rec: bad line:", line)
    }
    function apply(e) {
        var from = m.state, oldWarn = m.warn
        var next = Parse.recReduce(m, e, Date.now() / 1000)
        if (next === m) return
        m = next
        if (m.state !== from) transition(from, m.state)
        else if (m.state === "recording" && m.warn && m.warn !== oldWarn) notify(false, "Recording", Parse.recWarnText(m))
    }
    function transition(from, to) {
        guard.restart()
        if (to === "ask" && from === "stopping") menu.open = true            // only the instance that stopped asks
        else if (to === "error" && (from === "starting" || from === "stopping")) menu.open = true
        else if (menu.open && !(to === "ask" || to === "transcribing" || to === "error" || to === "recording")) menu.open = false
        if (to === "ask" && from === "recording" && m.stopReason && m.stopReason !== "user")
            notify(m.stopReason === "recorder_died" || m.stopReason === "low_disk", "Recording stopped", Parse.recStopText(m.stopReason))
        else if (from === "transcribing" && to === "done" && !K.Kozy.devMode)
            Quickshell.execDetached(Parse.recDoneArgv(m.session, m.transcript, Parse.recDoneBody(m)))
        else if (from === "transcribing" && to === "error")
            notify(true, "Transcription failed", m.msg)
    }
    // The live bar notifies; a dev-mode copy following the same helper would double up.
    function notify(critical, summary, body) {
        if (K.Kozy.devMode) return
        Quickshell.execDetached(["notify-send", "-a", "kozyrek", "-u", critical ? "critical" : "normal", summary, body])
    }

    function act(a) {
        switch (a) {
        case "start":  if (guard.running || cmd.running) return; apply({ ev: "local", state: "starting" }); run(["start"]); break
        case "stop":   if (guard.running || cmd.running) return; menu.open = false; apply({ ev: "local", state: "stopping" }); run(["stop"]); break
        case "popup":  menu.open = !menu.open; break
        case "open":   Quickshell.execDetached(["xdg-open", m.transcript]); verb(["dismiss"], "idle"); break
        case "folder": Quickshell.execDetached(["xdg-open", m.session || root.callsDir]); break
        }
    }
    // A verb with an optional optimistic state, shown until the helper answers.
    function verb(args, optimistic) {
        menu.open = false
        if (run(args) && optimistic) apply({ ev: "local", state: optimistic })
    }
    function transcribe() { verb(["transcribe", m.session], "transcribing") }
    function retry() { if (m.during === "transcribe") transcribe(); else { menu.open = false; act("start") } }

    // A Power.qml row. Uses only K.* and its own ids.
    component Choice: Rectangle {
        id: c
        property string glyph: ""
        property string label: ""
        property string hint: ""
        property color glyphColor: K.Theme.text
        property bool primary: false                      // the Enter default, filled like the current power profile
        signal picked()
        width: parent ? parent.width : 0; height: 36; radius: 10
        color: primary ? K.Theme.accent : (cma.containsMouse ? "#33ffffff" : "transparent")
        opacity: primary && cma.containsMouse ? 0.85 : 1
        Row {
            anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
            spacing: 10
            Text { text: c.glyph; color: c.primary ? "white" : c.glyphColor; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
            Text { text: c.label; color: c.primary ? "white" : K.Theme.text; font.pixelSize: K.Theme.fontPx; anchors.verticalCenter: parent.verticalCenter }
        }
        Text {
            visible: c.hint !== ""; text: c.hint; color: c.primary ? "white" : K.Theme.dim; font.pixelSize: 11
            anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
        }
        MouseArea { id: cma; anchors.fill: parent; hoverEnabled: true; onClicked: c.picked() }
    }

    // ── popup ───────────────────────────────────────────────
    K.Popup {
        id: menu
        pill: root
        contentWidth: 290
        wantsKeyboard: root.st === "ask"                  // Enter needs the bar on on-demand focus
        readonly property int colW: contentWidth - 32
        // Active focus on the ask section so Enter lands here; Esc falls through to
        // Popup.qml's close. "No" deletes the recording, so it is never a reflex key.
        onOpenChanged: if (open && root.st === "ask") Qt.callLater(() => ask.forceActiveFocus())

        Column {
            id: ask
            visible: root.st === "ask"
            width: menu.colW; spacing: 10
            Keys.onPressed: e => { if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) { root.transcribe(); e.accepted = true } }
            Text { text: "Turn recorded audio to text?"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; wrapMode: Text.Wrap; width: parent.width }
            Text { text: Parse.recAskLine(root.m); color: K.Theme.dim; font.pixelSize: 12; wrapMode: Text.Wrap; width: parent.width }
            Choice { primary: true; glyph: "\u{F113D}"; label: "Yes"; hint: "transcribe · Enter"; onPicked: root.transcribe() }
            Choice { glyph: "\u{F09E7}"; glyphColor: K.Theme.bad; label: "No"; hint: "delete recording"; onPicked: root.verb(["discard"], "idle") }
            Text { text: "Audio is kept 7 days, the transcript forever."; color: K.Theme.dim; font.pixelSize: 11 }
        }

        Column {
            visible: root.st === "transcribing"
            width: menu.colW; spacing: 10
            Row {
                width: parent.width
                Text { text: "Transcribing"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; width: parent.width - 50 }
                Text { text: Math.round(Math.max(0, root.m.frac) * 100) + "%"; color: K.Theme.text; font.pixelSize: K.Theme.fontPx; font.bold: true; width: 50; horizontalAlignment: Text.AlignRight }
            }
            Rectangle {                                     // the OSD's track
                width: parent.width; height: 6; radius: 3; color: K.Theme.faint
                Rectangle {
                    width: parent.width * Math.max(0, root.m.frac); height: parent.height; radius: 3; color: K.Theme.accent
                    Behavior on width { NumberAnimation { duration: 400 } }
                }
            }
            Grid {
                columns: 2; columnSpacing: 12; rowSpacing: 2
                Text { text: "stage";   color: K.Theme.dim; font.pixelSize: 12 }
                Text { text: Parse.recStageLabel(root.m.stage); color: K.Theme.text; font.pixelSize: 12 }
                Text { text: "elapsed"; color: K.Theme.dim; font.pixelSize: 12 }
                Text { text: Parse.fmtClock(root.now - root.m.since); color: K.Theme.text; font.pixelSize: 12 }
                Text { text: "left";    color: K.Theme.dim; font.pixelSize: 12 }
                Text { text: Parse.recEta(root.m, root.now); color: K.Theme.text; font.pixelSize: 12 }
                Text { text: "audio";   color: K.Theme.dim; font.pixelSize: 12 }
                Text { text: root.m.dur > 0 ? Parse.fmtClock(root.m.dur) : "—"; color: K.Theme.text; font.pixelSize: 12 }
            }
            Rectangle { width: parent.width; height: 1; color: K.Theme.faint }
            Choice { glyph: "\u{F0156}"; glyphColor: K.Theme.bad; label: "Cancel"; hint: "back to the question"; onPicked: root.verb(["cancel"], "") }
            Choice { glyph: "\u{F0770}"; label: "Open folder"; onPicked: { menu.open = false; root.act("folder") } }
        }

        Column {
            visible: root.st === "recording"
            width: menu.colW; spacing: 10
            Text { text: "Recording " + Parse.fmtClock(root.now - root.m.since); color: K.Theme.text; font.pixelSize: 15; font.bold: true }
            Grid {
                columns: 2; columnSpacing: 12; rowSpacing: 2
                Repeater {
                    model: Parse.recDetails(root.m)          // flat cells: ["mic", "ok", "echo cancel", "on", …]
                    Text { text: modelData; color: index % 2 ? (modelData.startsWith("⚠") ? K.Theme.warn : K.Theme.text) : K.Theme.dim; font.pixelSize: 12 }
                }
            }
            Text { text: "Local only: nothing leaves this laptop."; color: K.Theme.dim; font.pixelSize: 11 }
            Choice { glyph: "\u{F04DB}"; glyphColor: K.Theme.bad; label: "Stop"; onPicked: root.act("stop") }
            Choice { glyph: "\u{F0770}"; label: "Open folder"; onPicked: { menu.open = false; root.act("folder") } }
        }

        Column {
            visible: root.st === "error"
            width: menu.colW; spacing: 10
            Text { text: Parse.recErrorTitle(root.m); color: K.Theme.bad; font.pixelSize: 15; font.bold: true; wrapMode: Text.Wrap; width: parent.width }
            Text { text: root.m.msg; color: K.Theme.dim; font.pixelSize: 12; wrapMode: Text.Wrap; maximumLineCount: 4; elide: Text.ElideRight; width: parent.width }
            Choice { visible: root.m.retry && root.m.session !== ""; glyph: "\u{F0709}"; label: "Retry"; onPicked: root.retry() }
            Choice { visible: root.m.session !== ""; glyph: "\u{F09E7}"; glyphColor: K.Theme.bad; label: "Delete recording"; onPicked: root.verb(["discard"], "idle") }
            Choice { visible: root.m.session === "" || !root.m.retry; glyph: "\u{F0156}"; label: "Dismiss"; onPicked: root.verb(["dismiss"], "idle") }
        }
    }
}
