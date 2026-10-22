import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// TV remote overlay for a CVTE / MHShare TV: a touchpad that is the TV screen in miniature (touch
// the pad where you want the cursor, tap to click, wheel = d-pad Up/Down, right click = Back), plus
// d-pad, Back/Home/Menu and volume. tv/tvd.py owns the link to the TV; this draws the panel and talks
// to it over a Unix socket. The daemon is a child of the bar and opens the TV link only when used.
Scope {
    id: root
    property bool open: false
    property bool linked: false
    property string err: ""
    property bool mirroring: false
    property bool mirrorBusy: false
    property bool ytBusy: false
    property string ytMsg: ""
    signal ytOk()
    property var apps: []
    property string appBase: ""
    property bool mapped: false         // outlives `open` by the exit animation
    property real mr: 36                // panel offset from the screen's right / bottom edge, set by dragging
    property real mb: 36
    readonly property string sockPath: Quickshell.env("KOZYREK_TV_SOCK") || (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/kozyrek-tv.sock"   // same rule as tvd/tvctl

    function toggle() { open = !open }
    readonly property string jpgPath: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/kozyrek-tv.jpg"
    property bool viewing: false
    property bool big: false            // wider panel, larger TV picture (960x540 instead of 640x360: costs the TV more), fewer controls
    readonly property string viewSize: big ? "960x540" : "640x360"
    onBigChanged: if (open) send("view on " + viewSize)        // tvd restarts the picture at the new size
    property int frame: 0
    readonly property bool sockUp: sk.item !== null && sk.item.connected
    function send(line) { if (sockUp) { sk.item.write(line + "\n"); sk.item.flush() } }
    function key(names) { send("key " + names) }

    onOpenChanged: {
        if (open) { unmap.stop(); mapped = true; send("connect"); send("mirror status"); send("view on " + viewSize); if (apps.length === 0) send("apps") }
        else { unmap.restart(); send("view off") }
    }
    Timer { id: unmap; interval: 240; onTriggered: root.mapped = false }
    Timer { interval: 30000; repeat: true; running: root.open; onTriggered: { root.send("ping"); root.send("mirror status") } }
    Timer { interval: 3000; repeat: true; running: root.open && root.mirroring; onTriggered: root.send("mirror status") }   // a cancelled window picker ends the run quietly
    Timer { interval: 40; repeat: true; running: root.open && root.viewing; onTriggered: root.frame++ }                         // reload the TV picture file (25/s: the TV encodes ~20-25 fps at best)
    Timer { interval: 5000; repeat: true; running: root.open; onTriggered: root.send("view status") }                           // view.py can die (TV off, adb off)

    Process {
        id: daemon
        command: ["python3", Quickshell.shellDir + "/tv/tvd.py"]
        stdinEnabled: true
        running: true
        stderr: SplitParser { onRead: line => { console.log(line); if (line.startsWith("tvd: listening")) root.daemonUp = true } }
        onExited: { root.daemonUp = false; restart.start() }
    }
    Timer { id: restart; interval: 2000; onTriggered: daemon.running = true }
    property bool daemonUp: false       // set by tvd's "listening" line: connecting earlier only fails (and warns)

    // The daemon may start after us or restart under us. A Quickshell Socket that failed once never
    // connects again (verified: retries on the same object stay silent while the daemon is back up),
    // so a dead socket is thrown away and a fresh one created.
    Component {
        id: sockC
        Socket {
            path: root.sockPath
            connected: true
            parser: SplitParser {
                onRead: line => {
                    if (line.startsWith("err ")) { root.err = line.slice(4); root.linked = false; root.mirrorBusy = false }
                    else if (line === "ok") { root.err = ""; root.linked = true }
                    else if (line === "yt ok") { root.ytBusy = false; root.ytMsg = ""; root.ytOk() }
                    else if (line.startsWith("yt err ")) { root.ytBusy = false; root.ytMsg = line.slice(7) }
                    else if (line.startsWith("mirror ")) { root.mirroring = line === "mirror on"; root.mirrorBusy = false }
                    else if (line.startsWith("view ")) { root.viewing = line === "view on" }
                    else if (line.startsWith("{")) {
                        var a = JSON.parse(line)
                        root.appBase = a.base
                        root.apps = a.apps.filter((x, i, l) => l.findIndex(y => y.packName === x.packName) === i)
                    }
                }
            }
            onConnectedChanged: if (!connected) { root.linked = false; root.ytBusy = false }
        }
    }
    Loader { id: sk; active: root.daemonUp; sourceComponent: sockC }
    Timer {
        interval: 1000; repeat: true; running: root.daemonUp && !root.sockUp
        onTriggered: { sk.active = false; Qt.callLater(() => sk.active = root.daemonUp) }
    }

    component Btn: Rectangle {
        id: btn
        property string label
        property string cmd
        property bool hold: false           // volume: keep sending while pressed
        implicitWidth: 56; implicitHeight: 40
        radius: 10
        color: ma.pressed ? Theme.today : "#26ffffff"
        border.width: 1; border.color: Theme.faint
        Behavior on color { ColorAnimation { duration: 90 } }
        Text { anchors.centerIn: parent; text: btn.label; color: Theme.text; font.pixelSize: Theme.fontPx + 1 }
        MouseArea {
            id: ma
            anchors.fill: parent
            onPressed: { root.key(btn.cmd); if (btn.hold) rep.start() }
            onReleased: rep.stop()
            onCanceled: rep.stop()
        }
        Timer { id: rep; interval: 160; repeat: true; onTriggered: root.key(btn.cmd) }
    }

    PanelWindow {
        id: win
        visible: root.mapped
        anchors { top: true; bottom: true; left: true; right: true }
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        mask: Region { item: panel }
        WlrLayershell.namespace: "kozyrek-tv"
        WlrLayershell.layer: WlrLayer.Overlay
        // OnDemand: click the panel to type on the TV with the keyboard; it never grabs focus by itself.
        WlrLayershell.keyboardFocus: root.open ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None

        Rectangle {
            id: panel
            width: root.big ? 868 : 368; height: col.implicitHeight + 28
            x: win.width - width - root.mr
            y: win.height - height - root.mb
            radius: 18
            color: Theme.bg
            border.width: 1; border.color: "#2effffff"
            opacity: root.open ? 1 : 0
            scale: root.open ? 1 : 0.94
            transformOrigin: Item.BottomRight
            Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }

            focus: true
            Keys.onPressed: event => {
                var k = { [Qt.Key_Up]: "up", [Qt.Key_Down]: "down", [Qt.Key_Left]: "left", [Qt.Key_Right]: "right",
                          [Qt.Key_Return]: "ok", [Qt.Key_Enter]: "ok", [Qt.Key_Backspace]: "back",
                          [Qt.Key_Plus]: "volup", [Qt.Key_Equal]: "volup", [Qt.Key_Minus]: "voldown", [Qt.Key_M]: "mute",
                          [Qt.Key_H]: "home" }[event.key]
                if (event.key === Qt.Key_Escape) root.open = false
                else if (k) root.key(k)
                else return
                event.accepted = true
            }

            Column {
                id: col
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                spacing: 12

                Row {
                    spacing: 8
                    Rectangle {
                        width: 9; height: 9; radius: 5; anchors.verticalCenter: parent.verticalCenter
                        color: root.err ? Theme.bad : root.linked ? "#6cd98a" : Theme.warn
                    }
                    Text {
                        width: col.width - 17 - 8 - 22 - 8 - 22 - 8
                        text: root.err || (root.linked ? "TV connected" : root.sockUp ? "Connecting…" : "Waiting for tvd…")
                        color: root.err ? Theme.bad : Theme.dim
                        font.pixelSize: Theme.fontPx; elide: Text.ElideRight
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {                  // bigger / smaller: the picture 868 px wide with only pad, text field and Back/Home/Menu/Vol
                        width: 22; horizontalAlignment: Text.AlignHCenter
                        text: root.big ? "⤡" : "⤢"; color: Theme.dim; font.pixelSize: Theme.fontPx + 3
                        anchors.verticalCenter: parent.verticalCenter
                        MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: root.big = !root.big }
                    }
                    Text {                  // close (Esc also closes while the panel has keyboard focus)
                        width: 22; horizontalAlignment: Text.AlignHCenter
                        text: "✕"; color: Theme.dim; font.pixelSize: Theme.fontPx + 3
                        anchors.verticalCenter: parent.verticalCenter
                        MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: root.open = false }
                    }
                }

                // The pad is the TV screen: normalized position in = position on the TV out.
                Rectangle {
                    id: pad
                    width: col.width; height: width * 9 / 16
                    radius: 12; color: "#14ffffff"
                    border.width: 1; border.color: pm.pressed ? Theme.accent : Theme.faint
                    clip: true
                    // The TV's own screen (tv/view.py rewrites the file about 10x/s); the pad stays a touchpad on top of it.
                    // Two images take turns: the next frame loads into the hidden one and is shown only once it is ready, so a
                    // reload never blanks the picture (a single image dropped to "Loading" between frames and flickered).
                    Item {
                        id: vbuf
                        anchors.fill: parent
                        property int front: -1              // which image is on screen: 0 = a, 1 = b, -1 = none yet
                        Connections {
                            target: root
                            function onFrameChanged() {
                                if (!root.viewing) return
                                (vbuf.front === 0 ? ib : ia).source = "file://" + root.jpgPath + "?" + root.frame
                            }
                            function onViewingChanged() { if (!root.viewing) { vbuf.front = -1; ia.source = ""; ib.source = "" } }
                        }
                        Image {
                            id: ia
                            anchors.fill: parent; visible: vbuf.front === 0
                            cache: false; asynchronous: true; fillMode: Image.Stretch
                            onStatusChanged: if (status === Image.Ready && vbuf.front !== 0) vbuf.front = 0
                        }
                        Image {
                            id: ib
                            anchors.fill: parent; visible: vbuf.front === 1
                            cache: false; asynchronous: true; fillMode: Image.Stretch
                            onStatusChanged: if (status === Image.Ready && vbuf.front !== 1) vbuf.front = 1
                        }
                    }
                    Rectangle {
                        visible: pm.pressed
                        x: pm.mouseX - 10; y: pm.mouseY - 10; width: 20; height: 20; radius: 10
                        color: "transparent"; border.width: 2; border.color: Theme.accent
                    }
                    Text {
                        anchors.centerIn: parent; visible: !pm.pressed && !(root.viewing && vbuf.front >= 0)
                        text: root.viewing ? "waiting for the TV picture…" : "touchpad\nright click: back"; horizontalAlignment: Text.AlignHCenter
                        color: Theme.faint; font.pixelSize: Theme.fontPx
                    }
                    MouseArea {
                        id: pm
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        function pos() { return Math.max(0, Math.min(1, mouseX / width)).toFixed(4) + " " + Math.max(0, Math.min(1, mouseY / height)).toFixed(4) }
                        onPressed: m => { if (m.button === Qt.RightButton) root.key("back"); else root.send("touch d " + pos()) }
                        onPositionChanged: m => { if (pressedButtons & Qt.LeftButton) root.send("touch m " + pos()) }
                        onReleased: m => { if (m.button === Qt.LeftButton) root.send("touch u " + pos()) }
                        onCanceled: root.send("touch u " + pos())
                    }
                    // Scroll: the phone app has no scroll message either (capture 2026-10-19); it sends d-pad
                    // Up/Down, which scrolls pages in Brave and moves focus elsewhere. One key per wheel notch.
                    WheelHandler {
                        property real acc: 0
                        onWheel: ev => {
                            acc += ev.angleDelta.y
                            while (Math.abs(acc) >= 60) { root.key(acc > 0 ? "up" : "down"); acc -= acc > 0 ? 60 : -60 }
                        }
                    }
                }

                // Text for the TV's focused text field (search boxes, logins). Mirrors the field like the
                // phone app: every edit sends the whole content, so the TV field always equals this one.
                // Enter presses ENTER on the TV and starts a fresh line without wiping what was typed there.
                Rectangle {
                    width: col.width; height: 34; radius: 10; color: "#14ffffff"
                    border.width: 1; border.color: ti.activeFocus ? Theme.accent : Theme.faint
                    Text {
                        anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                        visible: ti.text === ""
                        text: "type to the TV…"; color: Theme.faint; font.pixelSize: Theme.fontPx + 1
                    }
                    TextInput {
                        id: ti
                        anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                        verticalAlignment: TextInput.AlignVCenter
                        color: Theme.text; font.pixelSize: Theme.fontPx + 1
                        clip: true; selectByMouse: true
                        property bool fresh: false
                        onTextChanged: if (!fresh) root.send("text " + text)
                        // Return must be consumed here: left alone it bubbles to the panel's key handler, which
                        // sends OK (23) right after ENTER, and on the TV's on-screen keyboard that types "й".
                        function submit(event) { root.key("enter"); fresh = true; text = ""; fresh = false; event.accepted = true }
                        Keys.onReturnPressed: event => submit(event)
                        Keys.onEnterPressed: event => submit(event)
                    }
                }

                Row {
                    spacing: 8; anchors.horizontalCenter: parent.horizontalCenter
                    Btn { label: "Back";  cmd: "back" }
                    Btn { label: "Home";  cmd: "home" }
                    Btn { label: "Menu";  cmd: "menu" }
                    Btn { label: "Vol −"; cmd: "voldown"; hold: true }
                    Btn { label: "Vol +"; cmd: "volup"; hold: true }
                }

                Grid {
                    visible: !root.big
                    columns: 3; spacing: 8; anchors.horizontalCenter: parent.horizontalCenter
                    Item { width: 56; height: 40 }
                    Btn { label: "▲"; cmd: "up" }
                    Btn { label: "Mute"; cmd: "mute" }
                    Btn { label: "◀"; cmd: "left" }
                    Btn { label: "OK"; cmd: "ok" }
                    Btn { label: "▶"; cmd: "right" }
                    Item { width: 56; height: 40 }
                    Btn { label: "▼"; cmd: "down" }
                    Item { width: 56; height: 40 }
                }

                Row {                       // laptop screen or one window on the TV (tv/mirror.py, started by tvd)
                    visible: !root.big
                    spacing: 8
                    Rectangle {
                        width: root.mirroring ? col.width : (col.width - 8) / 2; height: 34; radius: 10
                        color: root.mirroring || mm.pressed ? Theme.today : "#26ffffff"
                        Text {
                            anchors.centerIn: parent; color: Theme.text; font.pixelSize: Theme.fontPx + 1
                            text: root.mirrorBusy ? "…" : root.mirroring ? "Stop mirroring" : "Mirror screen"
                        }
                        MouseArea {
                            id: mm; anchors.fill: parent; enabled: !root.mirrorBusy
                            onClicked: { root.mirrorBusy = true; root.send("mirror " + (root.mirroring ? "off" : "on")) }
                        }
                    }
                    Rectangle {             // the picker is the compositor's own dialog; cancelling it ends the run
                        visible: !root.mirroring
                        width: (col.width - 8) / 2; height: 34; radius: 10
                        color: mw.pressed ? Theme.today : "#26ffffff"
                        Text { anchors.centerIn: parent; color: Theme.text; font.pixelSize: Theme.fontPx + 1; text: "Mirror a window" }
                        MouseArea {
                            id: mw; anchors.fill: parent; enabled: !root.mirrorBusy
                            onClicked: { root.mirrorBusy = true; root.send("mirror window") }
                        }
                    }
                }

                // A YouTube link or id (video or playlist). Enter opens it in the TV's own YouTube app, which streams it from YouTube:
                // nothing is sent from the laptop, so a ten-hour playlist costs nothing. The text stays until the TV accepted it, and the
                // reason shows under the field if not; this has its own state and never touches the TV link's "connected" / error line.
                Rectangle {
                    visible: !root.big
                    width: col.width; height: 34; radius: 10; color: "#14ffffff"
                    border.width: 1; border.color: yt.activeFocus ? Theme.accent : Theme.faint
                    Text {
                        anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                        visible: yt.text === ""
                        text: "YouTube link → play on the TV"; color: Theme.faint; font.pixelSize: Theme.fontPx + 1
                    }
                    TextInput {
                        id: yt
                        anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                        verticalAlignment: TextInput.AlignVCenter
                        color: Theme.text; font.pixelSize: Theme.fontPx + 1
                        clip: true; selectByMouse: true
                        function submit(event) {
                            event.accepted = true
                            if (root.ytBusy || text.trim() === "" || !root.sockUp) return
                            root.ytBusy = true; root.ytMsg = ""; root.send("yt " + text.trim())
                        }
                        Keys.onReturnPressed: event => submit(event)
                        Keys.onEnterPressed: event => submit(event)
                        // arrows left alone would bubble to the panel's key handler and move the TV's focus
                        Keys.onUpPressed: event => event.accepted = true
                        Keys.onDownPressed: event => event.accepted = true
                        Keys.onLeftPressed: event => event.accepted = true
                        Keys.onRightPressed: event => event.accepted = true
                        Connections { target: root; function onYtOk() { yt.text = "" } }
                    }
                }
                Text {
                    visible: !root.big && (root.ytBusy || root.ytMsg !== "")
                    width: col.width; wrapMode: Text.Wrap; font.pixelSize: Theme.fontPx
                    text: root.ytBusy ? "opening on the TV…" : root.ytMsg
                    color: root.ytBusy ? Theme.dim : Theme.bad
                }

                Rectangle {                 // installed apps, straight from the TV; click launches
                    width: col.width; height: 1; color: Theme.faint
                    visible: !root.big && root.apps.length > 0
                }
                ListView {
                    visible: !root.big
                    id: strip
                    width: col.width; height: root.apps.length > 0 ? 66 : 0
                    orientation: ListView.Horizontal; spacing: 6; clip: true
                    WheelHandler {          // a plain mouse wheel only has a vertical axis; map it to the strip
                        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                        onWheel: ev => strip.contentX = Math.max(0, Math.min(strip.contentWidth - strip.width,
                            strip.contentX - (ev.angleDelta.x || ev.angleDelta.y)))
                    }
                    model: root.apps
                    delegate: Item {
                        required property var modelData
                        width: 62; height: 66
                        Image {
                            id: ico
                            anchors { top: parent.top; horizontalCenter: parent.horizontalCenter }
                            width: 40; height: 40; sourceSize: Qt.size(80, 80)
                            source: root.appBase + "/" + modelData.icon
                            opacity: am.pressed ? 0.6 : 1
                        }
                        Text {
                            anchors { top: ico.bottom; topMargin: 3; left: parent.left; right: parent.right }
                            text: modelData.title; color: Theme.dim; font.pixelSize: 10
                            horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                        }
                        MouseArea { id: am; anchors.fill: parent; onClicked: root.send("launch " + modelData.packName) }
                    }
                }
            }

            // Drag the header to move the panel. The window covers the screen (click-through outside the
            // panel), so the panel moves inside a window that stays put and the pointer position is
            // stable; moving the layer surface itself made the pointer coordinates shift under the drag.
            MouseArea {
                width: parent.width - 90; height: 40                  // the right end stays free for the resize and close buttons
                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                property point last
                onPressed: m => { last = mapToItem(win.contentItem, m.x, m.y) }
                onPositionChanged: m => {
                    if (!pressed) return
                    var p = mapToItem(win.contentItem, m.x, m.y)
                    root.mr = Math.max(0, Math.min(win.width - panel.width, root.mr - (p.x - last.x)))
                    root.mb = Math.max(0, Math.min(win.height - panel.height, root.mb - (p.y - last.y)))
                    last = p
                }
            }
        }
    }
}
