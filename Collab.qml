import QtQuick
import Quickshell
import Quickshell.Io

// omp Collab sessions as a chat in the bar. collab/kozyrek-collab.ts joins the
// sessions (it holds the room links) and sends their transcripts as JSON lines
// over a Unix socket; this keeps the shown one in a ListModel for the popup in
// modules/Collab.qml. The helper runs as its own systemd user unit, so a reload
// of the bar keeps the sessions: the bar starts it when nobody answers.
Scope {
    id: root
    property bool open: false           // the popup; while it shows a session, that session sends no "finished" notifications
    property var hosts: []              // [{label, error, sessions: [{instanceId, title, cwd, model, busy, ask, access, watching}]}]
    property bool configured: false     // ~/.config/kozyrek/collab-hosts exists
    property var sessions: []           // joined sessions, see Session.meta() in the helper
    property string sid: ""             // shown session; "" = the session picker
    property string agent: ""           // shown subagent; "" = the main transcript
    property string err: ""
    property bool discovering: false
    readonly property var cur: sessions.find(s => s.sid === sid) ?? null
    readonly property var curAgent: cur && agent ? (cur.agents.find(a => a.id === agent) ?? null) : null
    property ListModel rows: ListModel {}
    property var live: []               // the assistant message being streamed, as rows
    property var expanded: ({})         // row id -> true for rows opened by a click
    property var _at: ({})              // row id -> index in `rows`
    signal grew()                      // rows or live rows changed: the popup keeps the view at the bottom

    readonly property string sockPath: Quickshell.env("KOZYREK_COLLAB_SOCK") || (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/kozyrek-collab.sock"
    readonly property string unit: Quickshell.env("KOZYREK_COLLAB_UNIT") || "kozyrek-collab"
    readonly property bool up: sk.item !== null && sk.item.connected

    function send(o) { if (up) { sk.item.write(JSON.stringify(o) + "\n"); sk.item.flush() } }
    function toggle() { open = !open }
    function show(s, a) {
        sid = s; agent = a || ""
        rows.clear(); _at = ({}); live = []; err = ""
        sendView()
        if (sid === "") discover()
    }
    function sendView() { send({ t: "view", sid: open ? sid : "", agent: agent }) }
    function discover() { discovering = true; send({ t: "discover" }) }
    function join(label, instanceId) { err = ""; send({ t: "join", label: label, instanceId: instanceId }); pendingJoin = label + "/" + instanceId }
    function joinLink(link) { err = ""; send({ t: "join", link: link }); var m = /([A-Za-z0-9_-]{10,64})[#.][A-Za-z0-9_-]+\s*$/.exec(link.trim()); pendingJoin = m ? "link/" + m[1] : "" }
    function leave(s) { send({ t: "leave", sid: s }); if (s === sid) show("", "") }
    function prompt(text) { if (agent) send({ t: "agent", sid: sid, cmd: "chat", agentId: agent, text: text }); else send({ t: "prompt", sid: sid, text: text }) }
    function abort() { send({ t: "abort", sid: sid }) }
    function answer(reqId, value) { send({ t: "answer", sid: sid, reqId: reqId, value: value }) }
    function agentCmd(cmd, id) { send({ t: "agent", sid: sid, cmd: cmd, agentId: id }) }
    function toggleRow(id) { var e = Object.assign({}, expanded); if (e[id]) delete e[id]; else e[id] = true; expanded = e }
    property string pendingJoin: ""     // opens the session once the helper lists it

    // Opening shows the session shown last time, else the first one, else the picker.
    onOpenChanged: {
        if (open && sid === "" && sessions.length > 0) show(sessions[0].sid, "")
        else { sendView(); if (open && sid === "") discover() }
    }
    onUpChanged: if (up) { downTicks = 0; sendView() }

    function append(list) {
        var at = _at
        for (var i = 0; i < list.length; i++) at[list[i].id] = rows.count + i
        rows.append(list)
    }
    function onMsg(m) {
        switch (m.t) {
        case "hosts": hosts = m.hosts; configured = m.configured; discovering = false; break
        case "sessions":
            sessions = m.list
            if (pendingJoin && m.list.some(s => s.sid === pendingJoin)) { var p = pendingJoin; pendingJoin = ""; show(p, "") }
            else if (sid && !m.list.some(s => s.sid === sid)) show("", "")
            break
        case "reset":
            if (m.sid !== sid || m.agent !== agent) return
            rows.clear(); _at = ({}); append(m.rows); grew()
            break
        case "rows":
            if (m.sid !== sid || m.agent !== agent) return
            for (var i = 0; i < m.patched.length; i++) {
                var at = _at[m.patched[i].id]
                if (at !== undefined) rows.set(at, m.patched[i])
            }
            append(m.added); grew()
            break
        case "live": if (m.sid === sid && agent === "") { live = m.rows; grew() } break
        case "error": err = m.message; pendingJoin = ""; discovering = false; break
        case "focus": open = true; show(m.sid, ""); break
        }
    }

    // The helper is started first (a second copy sees the first answering and
    // exits; systemd refuses a second unit of the same name anyway), and the socket
    // is tried a moment later, so a normal start logs no connection errors. A
    // Quickshell Socket that failed once never connects again, so a dead one is
    // replaced (same as Tv.qml); every 10 s without an answer starts the helper again.
    function startHelper() {
        Quickshell.execDetached([
            "systemd-run", "--user", "--quiet", "--collect", "--unit=" + root.unit,
            "-E", "PATH", "-E", "SSH_AUTH_SOCK", "-E", "KOZYREK_COLLAB_SOCK", "-E", "KOZYREK_COLLAB_HOSTS", "-E", "KOZYREK_COLLAB_NOTIFY",
            "bun", Quickshell.shellDir + "/collab/kozyrek-collab.ts"])
    }
    Component.onCompleted: startHelper()
    Component {
        id: sockC
        Socket {
            path: root.sockPath
            connected: true
            parser: SplitParser {
                onRead: line => { try { root.onMsg(JSON.parse(line)) } catch (e) { console.warn("collab: bad line", e) } }
            }
        }
    }
    Loader { id: sk; active: false; sourceComponent: sockC }
    property int downTicks: 0
    Timer {
        interval: 1000; repeat: true; running: !root.up
        onTriggered: {
            sk.active = false; Qt.callLater(() => sk.active = true)
            if (++root.downTicks % 10 === 0) root.startHelper()
        }
    }
    // The picker re-lists the hosts while it is on screen.
    Timer { interval: 15000; repeat: true; running: root.open && root.sid === "" && root.up; onTriggered: root.discover() }
}
