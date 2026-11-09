import QtQuick
import Quickshell
import Quickshell.Io
import "../" as K

// omp Collab chat: pill + popup. The data and the helper connection live in ../Collab.qml.
// Pill: robot, the number of watched sessions; lit while one works, yellow edge while one
// waits for an answer, a dot when one finished while you were not looking.
K.Pill {
    id: pill
    required property var svc
    readonly property var list: svc.sessions
    readonly property bool working: list.some(s => s.working)
    readonly property bool asking: list.some(s => s.ask !== null)
    readonly property bool unread: list.some(s => s.unread)

    active: pop.open
    loud: working || asking || unread
    edge: asking ? K.Theme.warn : K.Theme.faint
    onClicked: svc.toggle()

    Text {
        text: "\u{F06A9}"
        color: pill.asking ? K.Theme.warn : pill.working || pop.open ? K.Theme.text : K.Theme.dim
        font.pixelSize: 16; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
        SequentialAnimation on opacity {
            running: pill.working; loops: Animation.Infinite
            onRunningChanged: if (!running) parent.opacity = 1
            NumberAnimation { to: 0.35; duration: 650; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1; duration: 650; easing.type: Easing.InOutSine }
        }
    }
    Text {
        visible: pill.list.length > 0
        text: pill.list.length + (pill.unread ? " ●" : "")
        color: pop.open ? K.Theme.text : K.Theme.dim
        font.pixelSize: K.Theme.fontPx
        anchors.verticalCenter: parent.verticalCenter
    }

    Connections { target: pill.svc; function onOpenChanged() { pop.open = pill.svc.open } }

    // ── pieces ───────────────────────────────────────────────────────────────────

    component Btn: Rectangle {
        id: b
        property string label
        property color fg: K.Theme.text
        signal hit()
        width: bt.implicitWidth + 16; height: 24; radius: 8
        color: bma.containsMouse ? "#33ffffff" : "#1effffff"
        Text { id: bt; anchors.centerIn: parent; text: b.label; color: b.fg; font.pixelSize: 11 }
        MouseArea { id: bma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: b.hit() }
    }

    component Md: TextEdit {
        textFormat: TextEdit.MarkdownText
        readOnly: true; selectByMouse: true
        wrapMode: TextEdit.Wrap
        color: K.Theme.text; selectionColor: K.Theme.today; selectedTextColor: K.Theme.text
        font.pixelSize: K.Theme.fontPx
        onLinkActivated: link => Qt.openUrlExternally(link)
    }

    component Mono: TextEdit {
        readOnly: true; selectByMouse: true
        wrapMode: TextEdit.WrapAtWordBoundaryOrAnywhere
        color: K.Theme.text; selectionColor: K.Theme.today; selectedTextColor: K.Theme.text
        font.family: "monospace"; font.pixelSize: K.Theme.fontPx - 2
    }

    component Images: Flow {
        property string list: ""
        visible: list !== ""
        spacing: 6
        Repeater {
            model: parent.list === "" ? [] : parent.list.split("\n")
            Image {
                required property string modelData
                source: modelData
                fillMode: Image.PreserveAspectFit
                width: Math.min(sourceSize.width, 340); height: sourceSize.width > 0 ? width * sourceSize.height / sourceSize.width : 0
                asynchronous: true
            }
        }
    }

    // One transcript row; `r` is a ListModel item or a plain object (streamed rows).
    component RowView: Item {
        id: rv
        property var r
        readonly property string k: r ? r.k : ""
        readonly property bool ex: !!pill.svc.expanded[r ? r.id : ""]
        implicitHeight: box.implicitHeight + 8

        Column {
            id: box
            width: parent.width
            spacing: 4

            // you / a collab guest
            Rectangle {
                visible: rv.k === "user"
                anchors.right: parent.right
                width: parent.width - 48; implicitHeight: uc.implicitHeight + 16
                radius: 12; color: "#2eb69ad6"
                Column {
                    id: uc
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8 }
                    spacing: 2
                    Text { visible: text !== ""; text: rv.r ? rv.r.title : ""; color: K.Theme.accent; font.pixelSize: 10 }
                    Md { width: parent.width; text: rv.k === "user" ? rv.r.text : "" }
                    Images { width: parent.width; list: rv.k === "user" ? rv.r.images : "" }
                }
            }

            Md { visible: rv.k === "text"; width: parent.width; text: rv.k === "text" ? rv.r.text : "" }

            Text {
                visible: rv.k === "think"
                width: parent.width
                text: rv.k === "think" ? rv.r.text : ""
                color: K.Theme.dim; font.pixelSize: K.Theme.fontPx - 1; font.italic: true
                wrapMode: Text.Wrap
                maximumLineCount: rv.ex ? 100000 : 3; elide: Text.ElideRight
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.toggleRow(rv.r.id) }
            }

            // tool call: header line; click opens arguments and result. A running one shows its last lines.
            Rectangle {
                visible: rv.k === "tool"
                width: parent.width; implicitHeight: tc.implicitHeight + 12
                radius: 10; color: "#12ffffff"
                border.width: 1; border.color: rv.k === "tool" && rv.r.err ? K.Theme.bad : K.Theme.faint
                Column {
                    id: tc
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 6; leftMargin: 10; rightMargin: 10 }
                    spacing: 4
                    Item {
                        width: parent.width; height: 18
                        Text {
                            id: tg
                            text: rv.k !== "tool" ? "" : !rv.r.done ? "⋯" : rv.r.err ? "✗" : "✓"
                            color: rv.k !== "tool" ? K.Theme.dim : !rv.r.done ? K.Theme.warn : rv.r.err ? K.Theme.bad : K.Theme.dim
                            font.pixelSize: K.Theme.fontPx; anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            id: tn
                            x: tg.width + 6
                            text: rv.k === "tool" ? rv.r.title : ""
                            color: K.Theme.text; font.pixelSize: K.Theme.fontPx - 1; font.bold: true
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            x: tn.x + tn.width + 8; width: parent.width - x
                            text: rv.k === "tool" ? rv.r.sub : ""
                            color: K.Theme.dim; font.pixelSize: K.Theme.fontPx - 1; elide: Text.ElideRight
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.toggleRow(rv.r.id) }
                    }
                    Mono {
                        visible: rv.ex && rv.k === "tool" && rv.r.args !== "{}"
                        width: parent.width; color: K.Theme.dim
                        text: visible ? rv.r.args : ""
                    }
                    Mono {
                        readonly property string full: rv.k === "tool" ? rv.r.text : ""
                        visible: full !== "" && (rv.ex || !rv.r.done)
                        width: parent.width
                        color: rv.k === "tool" && rv.r.err ? K.Theme.bad : K.Theme.text
                        text: !visible ? "" : rv.ex ? full : full.split("\n").slice(-6).join("\n")
                    }
                    Images { width: parent.width; list: rv.k === "tool" && rv.ex ? rv.r.images : "" }
                }
            }

            // system notes, compaction and branch summaries: collapsed to a few lines
            Rectangle {
                visible: rv.k === "note"
                width: parent.width; implicitHeight: nc.implicitHeight + 12
                radius: 10; color: "transparent"; border.width: 1; border.color: K.Theme.faint
                clip: true
                Column {
                    id: nc
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 6; leftMargin: 10; rightMargin: 10 }
                    spacing: 2
                    Text {
                        text: (rv.k === "note" ? rv.r.title : "") + (rv.ex ? "  ▾" : "  ▸")
                        color: K.Theme.accent; font.pixelSize: 11
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.toggleRow(rv.r.id) }
                    }
                    Md {
                        visible: rv.ex
                        width: parent.width; color: K.Theme.dim
                        text: rv.k === "note" && rv.ex ? rv.r.text : ""
                    }
                    Images { width: parent.width; list: rv.k === "note" && rv.ex ? rv.r.images : "" }
                }
            }

            Text {
                visible: rv.k === "meta"
                width: parent.width; horizontalAlignment: Text.AlignHCenter
                text: rv.k === "meta" ? rv.r.text : ""
                color: K.Theme.dim; font.pixelSize: 11; wrapMode: Text.Wrap
            }
            Text {
                visible: rv.k === "error"
                width: parent.width
                text: rv.k === "error" ? rv.r.text : ""
                color: K.Theme.bad; font.pixelSize: K.Theme.fontPx - 1; wrapMode: Text.Wrap
            }
        }
    }

    // ── the popup ────────────────────────────────────────────────────────────────

    K.Popup {
        id: pop
        pill: pill
        contentWidth: 780
        wantsKeyboard: true
        readonly property int colW: contentWidth - 32
        readonly property var s: pill.svc.cur
        readonly property var a: pill.svc.curAgent
        readonly property bool canWrite: s !== null && !s.readOnly && s.phase !== "ended"
        onOpenChanged: {
            pill.svc.open = open
            if (open && pill.svc.sid !== "") Qt.callLater(() => input.forceActiveFocus())
        }

        // tabs: one per watched session, then + for the picker
        Flow {
            id: tabsFlow
            width: pop.colW
            spacing: 6
            Repeater {
                model: pill.list
                Rectangle {
                    id: tab
                    required property var modelData
                    readonly property bool on: pill.svc.sid === modelData.sid
                    width: tr.implicitWidth + 20; height: 28; radius: 9
                    color: on ? K.Theme.today : tma.containsMouse ? "#33ffffff" : "#1effffff"
                    border.width: 1; border.color: modelData.ask ? K.Theme.warn : "transparent"
                    Row {
                        id: tr
                        anchors.centerIn: parent
                        spacing: 6
                        Text {
                            text: tab.modelData.ask ? "❓" : tab.modelData.phase === "ended" ? "✕"
                                : tab.modelData.working ? "◌" : tab.modelData.unread ? "●" : "○"
                            color: tab.modelData.working ? K.Theme.accent : tab.modelData.phase === "ended" ? K.Theme.bad : K.Theme.dim
                            font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter
                            RotationAnimation on rotation { running: tab.modelData.working; from: 0; to: 360; duration: 1400; loops: Animation.Infinite }
                        }
                        Text {
                            text: tab.modelData.title; width: Math.min(implicitWidth, 170); elide: Text.ElideRight
                            color: K.Theme.text; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            text: "✕"; color: xma.containsMouse ? K.Theme.bad : K.Theme.dim; font.pixelSize: 10
                            anchors.verticalCenter: parent.verticalCenter
                            visible: tma.containsMouse || xma.containsMouse || tab.on
                            MouseArea { id: xma; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true; onClicked: pill.svc.leave(tab.modelData.sid) }
                        }
                    }
                    MouseArea { id: tma; anchors.fill: parent; z: -1; hoverEnabled: true; onClicked: pill.svc.show(tab.modelData.sid, "") }
                }
            }
            Rectangle {
                width: 28; height: 28; radius: 9
                color: pill.svc.sid === "" ? K.Theme.today : pma.containsMouse ? "#33ffffff" : "#1effffff"
                Text { anchors.centerIn: parent; text: "+"; color: K.Theme.text; font.pixelSize: 15 }
                MouseArea { id: pma; anchors.fill: parent; hoverEnabled: true; onClicked: pill.svc.show("", "") }
            }
        }

        // ── picker: sessions on the configured hosts, or a pasted link ──
        Column {
            visible: pill.svc.sid === ""
            width: pop.colW
            spacing: 8
            Row {
                spacing: 10
                Text { text: "Sessions"; color: K.Theme.text; font.pixelSize: 15; font.bold: true }
                Text {
                    text: !pill.svc.up ? "starting the helper…" : pill.svc.discovering ? "looking…" : "refresh"
                    color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.discover() }
                }
            }
            Text {
                visible: !pill.svc.configured
                width: pop.colW; wrapMode: Text.Wrap
                text: "No hosts yet. Add lines like  myserver = ssh myserver  to ~/.config/kozyrek/collab-hosts (an empty command means this machine), or paste a link below."
                color: K.Theme.dim; font.pixelSize: 12
            }
            Repeater {
                model: pill.svc.hosts
                Column {
                    id: hostCol
                    required property var modelData
                    width: pop.colW
                    spacing: 4
                    Text {
                        text: hostCol.modelData.label + (hostCol.modelData.error ? "  ·  " + hostCol.modelData.error
                            : hostCol.modelData.sessions.length === 0 ? "  ·  no shared sessions" : "")
                        color: hostCol.modelData.error ? K.Theme.bad : K.Theme.accent; font.pixelSize: 12
                        width: pop.colW; elide: Text.ElideRight
                    }
                    Repeater {
                        model: hostCol.modelData.sessions
                        Item {
                            id: hs
                            required property var modelData
                            width: pop.colW; height: 38
                            Text {
                                id: hd
                                text: hs.modelData.ask ? "❓" : hs.modelData.busy ? "◌" : "○"
                                color: hs.modelData.busy ? K.Theme.accent : K.Theme.dim; font.pixelSize: 12
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Column {
                                x: hd.width + 8; width: parent.width - x - hb.width - 8
                                anchors.verticalCenter: parent.verticalCenter
                                Text { width: parent.width; text: hs.modelData.title; color: K.Theme.text; font.pixelSize: 12; elide: Text.ElideRight }
                                Text {
                                    width: parent.width; elide: Text.ElideMiddle; color: K.Theme.dim; font.pixelSize: 10
                                    text: hs.modelData.cwd + "  ·  " + hs.modelData.model + (hs.modelData.access === "view" ? "  ·  view only" : "")
                                }
                            }
                            Btn {
                                id: hb
                                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                                label: hs.modelData.watching ? "open" : "watch"
                                onHit: hs.modelData.watching ? pill.svc.show(hostCol.modelData.label + "/" + hs.modelData.instanceId, "")
                                                             : pill.svc.join(hostCol.modelData.label, hs.modelData.instanceId)
                            }
                        }
                    }
                }
            }
            Rectangle {
                width: pop.colW; height: 32; radius: 10; color: "#14ffffff"
                border.width: 1; border.color: linkIn.activeFocus ? K.Theme.accent : K.Theme.faint
                Text {
                    anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                    visible: linkIn.text === ""; text: "paste a collab link, Enter joins"; color: K.Theme.faint; font.pixelSize: 12
                }
                TextInput {
                    id: linkIn
                    anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                    verticalAlignment: TextInput.AlignVCenter
                    color: K.Theme.text; font.pixelSize: 12; clip: true; selectByMouse: true
                    echoMode: TextInput.Password; passwordCharacter: "•"     // it is a secret
                    onAccepted: { if (text.trim()) pill.svc.joinLink(text.trim()); text = "" }
                }
            }
        }

        // ── a session ──
        Column {
            visible: pill.svc.sid !== ""
            width: pop.colW
            spacing: 8

            // what it is: folder · model · context · who is here; or the subagent shown
            Item {
                width: pop.colW; height: 18
                Text {
                    id: back
                    visible: pill.svc.agent !== ""
                    text: "← main"
                    color: K.Theme.accent; font.pixelSize: 12
                    anchors.verticalCenter: parent.verticalCenter
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.show(pill.svc.sid, "") }
                }
                Text {
                    x: back.visible ? back.width + 10 : 0
                    width: parent.width - x - agentBtns.width - 8
                    anchors.verticalCenter: parent.verticalCenter
                    elide: Text.ElideMiddle; font.pixelSize: 11
                    color: pop.s && pop.s.phase !== "live" ? K.Theme.warn : K.Theme.dim
                    text: {
                        var s = pop.s
                        if (!s) return ""
                        if (pop.a) return pop.a.name + " · " + pop.a.status + (pop.a.task ? " · " + pop.a.task : "")
                        if (s.phase === "ended") return "ended: " + s.ended
                        if (s.phase === "connecting" || s.phase === "waiting") return "connecting…"
                        if (s.phase === "reconnecting") return "reconnecting…"
                        if (s.loading) return "loading the transcript " + s.loading
                        return [s.cwd, s.model + (s.thinking ? " (" + s.thinking + ")" : ""),
                                s.ctx >= 0 ? Math.round(s.ctx) + "% context" : "",
                                s.people.join(", "), s.readOnly ? "view only" : ""].filter(x => x).join("  ·  ")
                    }
                }
                Row {
                    id: agentBtns
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    spacing: 4
                    Btn { visible: !!pop.a && pop.canWrite && pop.a.status === "running"; label: "kill"; fg: K.Theme.bad; onHit: pill.svc.agentCmd("kill", pop.a.id) }
                    Btn { visible: !!pop.a && pop.canWrite && (pop.a.status === "parked" || pop.a.status === "aborted"); label: "revive"; onHit: pill.svc.agentCmd("revive", pop.a.id) }
                }
            }

            // subagents of this session
            Flow {
                id: agentsFlow
                visible: pill.svc.agent === "" && pop.s !== null && pop.s.agents.length > 0
                width: pop.colW
                spacing: 6
                Repeater {
                    model: pop.s ? pop.s.agents : []
                    Rectangle {
                        id: ag
                        required property var modelData
                        width: Math.min(agr.implicitWidth + 16, pop.colW); height: 24; radius: 8
                        color: agma.containsMouse ? "#33ffffff" : "#1effffff"
                        Row {
                            id: agr
                            anchors.centerIn: parent
                            spacing: 6
                            Rectangle {
                                width: 7; height: 7; radius: 4; anchors.verticalCenter: parent.verticalCenter
                                color: ag.modelData.status === "running" ? K.Theme.accent : ag.modelData.status === "aborted" ? K.Theme.bad
                                     : ag.modelData.status === "parked" ? K.Theme.warn : K.Theme.dim
                            }
                            Text {
                                // omp names most subagents just "task"; what it was asked to do says more
                                text: (ag.modelData.task || ag.modelData.name) + (ag.modelData.status === "running" && (ag.modelData.intent || ag.modelData.tool) ? " · " + (ag.modelData.intent || ag.modelData.tool) : "")
                                width: Math.min(implicitWidth, 260); elide: Text.ElideRight
                                color: K.Theme.text; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        MouseArea { id: agma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.show(pill.svc.sid, ag.modelData.id) }
                    }
                }
            }

            ListView {
                id: lv
                width: pop.colW
                // The rest of the screen under the bar, at most 62 % of it: the popup must
                // never outgrow the screen, or it is placed off it.
                readonly property real screenH: K.Kozy.bar?.screen?.height ?? 900
                height: Math.round(Math.max(140, Math.min(screenH * 0.62, screenH - K.Theme.barH - 150 - tabsFlow.height
                    - (agentsFlow.visible ? agentsFlow.height + 8 : 0) - (askBox.visible ? askBox.height + 8 : 0)
                    - (composer.visible ? composer.height + 8 : 0) - (quota.visible ? quota.height + 10 : 0))))
                clip: true
                spacing: 4
                cacheBuffer: 2000
                boundsBehavior: Flickable.StopAtBounds
                model: pill.svc.rows
                delegate: RowView { width: lv.width; r: model }
                footer: Column {
                    width: lv.width
                    Repeater { model: pill.svc.live; RowView { required property var modelData; width: lv.width; r: modelData } }
                    Text {
                        visible: pill.svc.agent === "" && pop.s !== null && pop.s.working && pill.svc.live.length === 0
                        text: pop.s && pop.s.aborting ? "stopping…" : "working…"
                        color: K.Theme.dim; font.pixelSize: 11; font.italic: true
                    }
                }
                // Follow new output while the view is at the bottom; scrolling up stops that.
                // Opening the popup re-sends the whole transcript (a reset clears the model
                // and puts the view at the top), so opening always starts at the bottom.
                // Rows size themselves after the jump (markdown, images), so a jump is not
                // enough: keep pinning to the end while `stick` holds and the height grows.
                property bool stick: true
                function toEnd() { Qt.callLater(() => { if (stick) positionViewAtEnd() }) }
                onMovementStarted: stick = false
                onMovementEnded: stick = atYEnd
                onContentHeightChanged: toEnd()
                onHeightChanged: toEnd()
                Connections {
                    target: pill.svc
                    function onGrew() { lv.toEnd() }
                    function onSidChanged() { lv.stick = true }
                    function onAgentChanged() { lv.stick = true }
                }
                Connections { target: pop; function onOpenChanged() { if (pop.open) { lv.stick = true; lv.toEnd() } } }
                Rectangle {
                    parent: lv          // not the scrolling contentItem, where Flickable puts its children
                    z: 2
                    visible: !lv.atYEnd && lv.count > 0
                    anchors { right: parent.right; bottom: parent.bottom; margins: 8 }
                    width: 32; height: 32; radius: 16
                    color: tbMa.containsMouse ? K.Theme.accent : K.Theme.bg
                    border.width: 1; border.color: K.Theme.faint
                    Text { anchors.centerIn: parent; text: "\u{F0045}"; color: K.Theme.text; font.pixelSize: 18; font.family: K.Theme.iconFont }   // arrow down
                    MouseArea { id: tbMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { lv.stick = true; lv.positionViewAtEnd(); lv.toEnd() } }
                }
                Text {
                    anchors.centerIn: parent
                    visible: lv.count === 0 && pill.svc.live.length === 0
                    text: pill.svc.agent !== "" && pop.a && !pop.a.file ? "this agent has no transcript" : "nothing here yet"
                    color: K.Theme.faint; font.pixelSize: 12
                }
            }

            // the agent asks something (select or text), first writer to answer settles it
            Rectangle {
                id: askBox
                readonly property var q: pop.s && pill.svc.agent === "" ? pop.s.ask : null
                visible: q !== null && pop.canWrite
                width: pop.colW; implicitHeight: qc.implicitHeight + 16
                radius: 12; color: "#1ae8b34a"; border.width: 1; border.color: K.Theme.warn
                Column {
                    id: qc
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 12; rightMargin: 12 }
                    spacing: 6
                    Text { width: parent.width; wrapMode: Text.Wrap; text: askBox.q ? askBox.q.title : ""; color: K.Theme.text; font.pixelSize: 13; font.bold: true }
                    Text { visible: text !== ""; width: parent.width; wrapMode: Text.Wrap; text: askBox.q ? askBox.q.help : ""; color: K.Theme.dim; font.pixelSize: 11 }
                    Repeater {
                        model: askBox.q && askBox.q.kind === "select" ? askBox.q.options : []
                        Rectangle {
                            id: opt
                            required property var modelData
                            width: qc.width; implicitHeight: oc.implicitHeight + 10; radius: 8
                            color: oma.containsMouse ? "#33ffffff" : "#1effffff"
                            Column {
                                id: oc
                                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 10; rightMargin: 10 }
                                Text { width: parent.width; wrapMode: Text.Wrap; text: opt.modelData.mark + opt.modelData.label; color: K.Theme.text; font.pixelSize: 12 }
                                Text { visible: text !== ""; width: parent.width; wrapMode: Text.Wrap; text: opt.modelData.desc; color: K.Theme.dim; font.pixelSize: 10 }
                            }
                            MouseArea { id: oma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: pill.svc.answer(askBox.q.reqId, opt.modelData.label) }
                        }
                    }
                    Rectangle {
                        visible: !!askBox.q && askBox.q.kind === "editor"
                        width: qc.width; height: Math.min(160, ed.contentHeight + 14); radius: 8; color: "#14ffffff"
                        border.width: 1; border.color: ed.activeFocus ? K.Theme.accent : K.Theme.faint
                        TextEdit {
                            id: ed
                            anchors { fill: parent; margins: 7 }
                            wrapMode: TextEdit.Wrap; color: K.Theme.text; font.pixelSize: 12; selectByMouse: true
                            readonly property int req: askBox.q ? askBox.q.reqId : -1
                            onReqChanged: text = askBox.q ? askBox.q.prefill : ""
                        }
                    }
                    Row {
                        spacing: 6
                        Btn { visible: !!askBox.q && askBox.q.kind === "editor"; label: "send"; onHit: pill.svc.answer(askBox.q.reqId, ed.text) }
                        Btn { label: "cancel"; fg: K.Theme.dim; onHit: pill.svc.answer(askBox.q.reqId, undefined) }
                    }
                }
            }

            // composer: Enter sends, Shift+Enter is a new line
            Row {
                id: composer
                visible: pop.canWrite
                width: pop.colW
                spacing: 8
                Rectangle {
                    width: pop.colW - (stopBtn.visible ? stopBtn.width + 8 : 0) - sendBtn.width - 8
                    height: Math.max(34, Math.min(150, input.contentHeight + 16)); radius: 10; color: "#14ffffff"
                    border.width: 1; border.color: input.activeFocus ? K.Theme.accent : K.Theme.faint
                    Text {
                        anchors { left: parent.left; leftMargin: 10; top: parent.top; topMargin: 8 }
                        visible: input.text === ""
                        text: pill.svc.agent !== "" ? "message this subagent…" : "message the agent…"
                        color: K.Theme.faint; font.pixelSize: K.Theme.fontPx
                    }
                    Flickable {
                        id: fl
                        anchors { fill: parent; margins: 8; leftMargin: 10; rightMargin: 10 }
                        contentHeight: input.contentHeight; clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        TextEdit {
                            id: input
                            width: fl.width
                            wrapMode: TextEdit.Wrap; color: K.Theme.text; font.pixelSize: K.Theme.fontPx; selectByMouse: true
                            onCursorRectangleChanged: if (cursorRectangle.y + cursorRectangle.height > fl.contentY + fl.height) fl.contentY = cursorRectangle.y + cursorRectangle.height - fl.height
                            function submit() { if (text.trim()) { pill.svc.prompt(text); text = "" } }
                            Keys.onReturnPressed: event => { if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return } submit(); event.accepted = true }
                            Keys.onEnterPressed: event => { submit(); event.accepted = true }
                        }
                    }
                }
                Btn { id: stopBtn; visible: pill.svc.agent === "" && pop.s !== null && pop.s.working; label: "stop"; fg: K.Theme.bad; height: 34; onHit: pill.svc.abort() }
                Btn { id: sendBtn; label: "send"; height: 34; onHit: input.submit() }
            }
        }

        // Quota left, the same rows as omp's line under its editor: its usage-left
        // extension writes them to $XDG_RUNTIME_DIR/omp-usage.json every 30 s while
        // an omp TUI runs. Older than 5 minutes = no omp running, shown dimmed.
        Column {
            id: quota
            property var info: null
            property real now: Date.now()
            readonly property bool stale: !info || now - info.at > 300000
            visible: !!info && info.rows.length > 0
            width: pop.colW
            spacing: 2
            opacity: stale ? 0.45 : 1
            function dur(ms) {
                const mins = Math.max(0, Math.round(ms / 60000))
                if (mins < 60) return mins + "m"
                const h = Math.floor(mins / 60)
                if (h < 24) return h + "h" + (mins % 60 ? (mins % 60) + "m" : "")
                return Math.floor(h / 24) + "d" + (h % 24 ? (h % 24) + "h" : "")
            }
            function tone(left) { return left <= 20 ? K.Theme.bad : left <= 50 ? K.Theme.warn : K.Theme.good }
            Timer { running: pop.open; repeat: true; interval: 30000; triggeredOnStart: true; onTriggered: quota.now = Date.now() }
            FileView {
                path: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/omp-usage.json"
                watchChanges: true
                printErrors: false      // absent until an omp TUI has run
                onFileChanged: reload()
                onLoaded: { try { quota.info = JSON.parse(text()) } catch (e) {} }
            }
            Repeater {
                model: quota.info ? quota.info.rows : []
                Row {
                    id: qr
                    required property var modelData
                    spacing: 6
                    Text { text: qr.modelData.account ?? ""; width: 170; elide: Text.ElideRight; color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                    Repeater {
                        model: qr.modelData.meters
                        Row {
                            id: qm
                            required property var modelData
                            required property int index
                            spacing: 5
                            Text { visible: qm.index > 0; text: "·"; color: K.Theme.faint; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: qm.modelData.label; color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                            Rectangle {
                                width: 56; height: 3; radius: 2; color: K.Theme.faint; anchors.verticalCenter: parent.verticalCenter
                                Rectangle { width: parent.width * qm.modelData.leftPct / 100; height: parent.height; radius: 2; color: quota.tone(qm.modelData.leftPct) }
                            }
                            Text { text: qm.modelData.leftPct + "%"; color: quota.tone(qm.modelData.leftPct); font.pixelSize: 11; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
                            Text {
                                visible: qm.modelData.resetsAt !== undefined
                                text: "↻" + quota.dur(qm.modelData.resetsAt - quota.now)
                                color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }
            }
        }

        Text {
            visible: pill.svc.err !== ""
            width: pop.colW; wrapMode: Text.Wrap
            text: pill.svc.err; color: K.Theme.bad; font.pixelSize: 11
        }
    }
}
