import QtQuick
import Quickshell
import Quickshell.Io
import "../" as K

// КРЕПОСТНОЙ: the unattended work agent. Shows what it is doing; the runner
// writes status.json (atomic rename) and runs as the user unit `krepostnoy`.
// Click: list of recent runs. Right click: start / stop the unit.
K.Pill {
    id: root
    readonly property string statusPath: Quickshell.env("KOZYREK_KREPOSTNOY_STATUS")
        || Quickshell.env("HOME") + "/.local/state/krepostnoy/status.json"
    readonly property string unit: "krepostnoy"

    property var status: ({})     // parsed status.json, {} while absent
    property bool unitOn: false
    property real now: Date.now()
    property string ack: ""       // `since` of the outcome the popup was last opened for
    property var hidden: []       // run ids removed from the list with ✕ (state/krepostnoy-hidden.json)

    readonly property var runs: status.runs || []
    readonly property var shown: runs.filter(r => !hidden.includes(r.id))
    readonly property var last: runs.length ? runs[0] : null
    readonly property string st: status.state || "listening"
    readonly property real sinceMs: status.since ? Date.parse(status.since) : 0
    readonly property bool acked: ack === (status.since || "")
    // off | listening | working | done | nothing | stuck | dead
    readonly property string vs: !unitOn ? "off"
        : st === "working" ? "working"
        : (st === "done" || st === "stuck" || st === "dead") && !acked ? st
        : st === "nothing" && now - sinceMs < 10000 ? "nothing"
        : "listening"
    readonly property int commits: last && last.commits ? last.commits.length : 0

    active: menu.open || vs === "done"
    loud: vs !== "off" && vs !== "listening"
    progress: vs === "working" && status.deadline
        ? (now - sinceMs) / (Date.parse(status.deadline) - sinceMs) : -1
    edge: vs === "stuck" ? K.Theme.warn : K.Theme.faint
    color: vs === "dead" ? K.Theme.bad : active ? K.Theme.accent : hovered ? "#33ffffff" : "#1effffff"

    function plural(n, one, few, many) {
        var m = n % 100, d = n % 10
        return m >= 11 && m <= 14 ? many : d === 1 ? one : d >= 2 && d <= 4 ? few : many
    }
    function commitWord(n) { return plural(n, "КОММИТ", "КОММИТА", "КОММИТОВ") }
    readonly property string label: ({
        off: "☠",
        listening: "⛓ КРЕПОСТНОЙ",
        working: "⚒ ПАШЕТ ЗА ТЕБЯ",
        done: commits > 0 ? "✔ " + commits + " " + commitWord(commits) + " · ТЫ НЕ НУЖЕН" : "✔ СДЕЛАЛ · ТЫ НЕ НУЖЕН",
        nothing: "¯\\_(ツ)_/¯ ОТМАЗАЛСЯ",
        stuck: "✋ ЗАБУКСОВАЛ · ПОМОГИ",
        dead: "☠ СМЕРТЬ В НИЩЕТЕ"
    })[vs]

    function reload() { statusFile.reload() }
    function hide(id) {
        if (last && id === last.id) ack = status.since || ""   // removing the outcome also dismisses it
        // keep only ids the runner still lists, so the file cannot grow forever
        hidden = hidden.filter(h => runs.some(r => r.id === h)).concat([id])
        hiddenStore.setText(JSON.stringify(hidden))
    }
    function check() { if (!isActive.running) isActive.running = true }
    function toggleUnit() {
        ctl.command = ["systemctl", "--user", unitOn ? "stop" : "start", unit]
        ctl.running = true
    }
    function omp(dir) { Quickshell.execDetached(["kitty", "--directory", Quickshell.env("HOME") + "/work", "omp", "--session-dir", dir, "-c"]) }
    function diff(commits) {   // one kitty per repo
        var byRepo = {}
        commits.forEach(c => (byRepo[c.repo] = byRepo[c.repo] || []).push(c.sha))
        Object.keys(byRepo).forEach(r => Quickshell.execDetached(["kitty", "--hold", "git", "-C", r, "show", "--stat", "-p"].concat(byRepo[r])))
    }
    function repos(commits) {
        var seen = []
        commits.forEach(c => { var b = c.repo.split("/").pop(); if (!seen.includes(b)) seen.push(b) })
        return seen.join(", ")
    }

    onClicked: button => { if (button === Qt.RightButton) toggleUnit(); else menu.open = !menu.open }

    // Opening the popup acknowledges the current outcome.
    Connections { target: menu; function onOpenChanged() { if (menu.open) root.ack = root.status.since || "" } }

    FileView {
        id: statusFile
        path: root.statusPath
        watchChanges: true
        printErrors: false      // absent until the first run
        onFileChanged: reload()
        onLoaded: { try { root.status = JSON.parse(text()) } catch (e) { root.status = ({}) } }
        onLoadFailed: root.status = ({})
    }
    FileView {
        id: hiddenStore
        path: Quickshell.statePath("krepostnoy-hidden.json")
        printErrors: false      // absent until the first ✕
        onLoaded: { try { root.hidden = JSON.parse(text()) } catch (e) {} }
    }
    Process {
        id: isActive
        command: ["systemctl", "--user", "is-active", "--quiet", root.unit]
        onExited: code => root.unitOn = code === 0
    }
    Process { id: ctl; onExited: root.check() }
    // Polling also re-reads the file: a rename over the watched inode can drop the watch.
    Timer { interval: 5000; running: true; repeat: true; triggeredOnStart: true; onTriggered: { root.check(); root.reload() } }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = Date.now() }

    SequentialAnimation on opacity {
        running: root.vs === "stuck"
        loops: Animation.Infinite
        NumberAnimation { to: 0.45; duration: 700; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
    }
    onVsChanged: if (vs !== "stuck") opacity = 1

    Text {
        text: root.label
        color: root.vs === "dead" ? "white" : root.vs === "done" ? K.Theme.bg : root.vs === "stuck" ? K.Theme.warn
             : root.vs === "working" || menu.open ? K.Theme.text : K.Theme.dim
        font.pixelSize: K.Theme.fontPx
        font.bold: root.vs === "done" || root.vs === "stuck" || root.vs === "dead"
        anchors.verticalCenter: parent.verticalCenter
    }

    K.Popup {
        id: menu
        pill: root
        contentWidth: 400
        readonly property int colW: contentWidth - 32

        Row {
            width: menu.colW
            Text { text: "⛓ КРЕПОСТНОЙ"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; width: menu.colW - stateTxt.width }
            Text {
                id: stateTxt
                text: !root.unitOn ? "не работает" : root.st === "working" ? "пашет" : "слушает"
                color: K.Theme.dim; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
            }
        }

        Text { visible: root.shown.length === 0; text: "пока ничего не делал"; color: K.Theme.dim; font.pixelSize: 12 }

        Repeater {
            model: root.shown.slice(0, 8)
            Item {
                id: row
                required property var modelData
                readonly property var cs: modelData.commits || []
                readonly property string kind: modelData.state
                readonly property string time: Qt.formatDateTime(new Date(modelData.started), "hh:mm")
                readonly property string head: time + " · " + (modelData.who ? modelData.who + " → " : "") + (modelData.gist || modelData.summary || "")
                width: menu.colW; height: 42
                Column {
                    anchors { left: parent.left; right: btns.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
                    Text { width: parent.width; elide: Text.ElideRight; text: row.head; color: K.Theme.text; font.pixelSize: 12 }
                    Text {
                        width: parent.width; elide: Text.ElideRight; font.pixelSize: 11
                        color: row.kind === "done" ? K.Theme.accent : row.kind === "stuck" ? K.Theme.warn
                             : row.kind === "dead" ? K.Theme.bad : K.Theme.dim
                        text: row.kind === "done" ? "✔ " + row.cs.length + " " + root.commitWord(row.cs.length).toLowerCase() + " · " + root.repos(row.cs)
                            : row.kind === "nothing" ? "¯\\_(ツ)_/¯ " + (modelData.summary || "")
                            : row.kind === "stuck" ? "✋ " + (modelData.summary || "")
                            : row.kind === "dead" ? "☠ " + (modelData.summary || "")
                            : "⚒ пашет…"
                    }
                }
                Row {
                    id: btns
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    spacing: 4
                    Repeater {
                        model: [
                            { label: row.kind === "stuck" ? "ответить" : "omp", show: !!row.modelData.session_dir, run: () => root.omp(row.modelData.session_dir) },
                            { label: "diff", show: row.cs.length > 0, run: () => root.diff(row.cs) },
                            { label: "✕", show: row.kind !== "working", run: () => root.hide(row.modelData.id) }
                        ]
                        Rectangle {
                            visible: modelData.show
                            width: btnTxt.implicitWidth + 16; height: 24; radius: 8
                            color: btnMA.containsMouse ? "#33ffffff" : "#1effffff"
                            Text { id: btnTxt; anchors.centerIn: parent; text: modelData.label; color: K.Theme.text; font.pixelSize: 11 }
                            MouseArea { id: btnMA; anchors.fill: parent; hoverEnabled: true; onClicked: modelData.run() }
                        }
                    }
                }
            }
        }

        Text { text: "ЛКМ список · ПКМ уволить/нанять"; color: K.Theme.dim; font.pixelSize: 10 }
    }
}
