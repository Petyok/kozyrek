import QtQuick
import Quickshell
import Quickshell.Wayland
import "../" as K
import "../lib/parse.js" as Parse

// Area recording: dims the screen around the recorded rectangle and floats the
// recording controls just outside it. wf-recorder crops the composited output,
// overlays included, so nothing here may touch the rectangle itself: the dim is
// four bands around it, the red outline sits in the 2 px ring outside it, and the
// controls go above / below / beside it (Parse.screenrecCtlPos). Input passes
// through everywhere but the controls.
PanelWindow {
    id: frame
    required property var rec                               // the ScreenRec pill: state + stop() / toggleMic()
    readonly property var area: rec.area
    // The output holding the area's centre; slurp and wf-recorder speak layout coordinates.
    readonly property var scr: {
        var s = Quickshell.screens
        if (area) {
            var cx = area.x + area.w / 2, cy = area.y + area.h / 2
            for (var i = 0; i < s.length; i++)
                if (cx >= s[i].x && cx < s[i].x + s[i].width && cy >= s[i].y && cy < s[i].y + s[i].height) return s[i]
        }
        return s.length ? s[0] : null
    }
    readonly property var r: area && scr ? { x: area.x - scr.x, y: area.y - scr.y, w: area.w, h: area.h } : { x: 0, y: 0, w: 0, h: 0 }
    readonly property var pos: Parse.screenrecCtlPos(r, ctl.width, ctl.height, scr ? scr.width : 0, scr ? scr.height : 0)
    readonly property color shade: "#80000000"

    screen: scr
    visible: rec.pid > 0 && area !== null && !rec.stopping
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore                     // over the bar too: everything but the area is dimmed
    mask: Region { item: ctl }
    WlrLayershell.namespace: "kozyrek-screenrec"
    WlrLayershell.layer: WlrLayer.Overlay                   // over fullscreen windows as well
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    // above, below, left, right of the area
    Rectangle { x: 0; y: 0; width: frame.width; height: Math.max(0, frame.r.y); color: frame.shade }
    Rectangle { x: 0; y: frame.r.y + frame.r.h; width: frame.width; height: Math.max(0, frame.height - frame.r.y - frame.r.h); color: frame.shade }
    Rectangle { x: 0; y: frame.r.y; width: Math.max(0, frame.r.x); height: frame.r.h; color: frame.shade }
    Rectangle { x: frame.r.x + frame.r.w; y: frame.r.y; width: Math.max(0, frame.width - frame.r.x - frame.r.w); height: frame.r.h; color: frame.shade }
    Rectangle {                                             // a border paints inside its rect: this ring is all outside the area
        x: frame.r.x - 2; y: frame.r.y - 2; width: frame.r.w + 4; height: frame.r.h + 4
        color: "transparent"; border.width: 2; border.color: K.Theme.bad
    }

    Rectangle {
        id: ctl
        x: frame.pos.x; y: frame.pos.y
        width: buttons.implicitWidth + 16; height: buttons.implicitHeight + 12
        radius: K.Theme.radius + 2
        color: K.Theme.bg
        border.width: 1; border.color: K.Theme.faint

        Row {
            id: buttons
            anchors.centerIn: parent
            spacing: 8
            leftPadding: 4
            Text {
                text: "\u{F0567}"
                color: K.Theme.bad
                opacity: frame.rec.blink ? 0.35 : 1
                Behavior on opacity { NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } }
                font.pixelSize: 16; font.family: K.Theme.iconFont
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                id: clock
                text: Parse.fmtClock(frame.rec.now - frame.rec.since)
                width: Math.max(implicitWidth, pin.width)       // no twitch every second
                color: K.Theme.text
                font.pixelSize: K.Theme.fontPx; font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }
            TextMetrics { id: pin; font: clock.font; text: clock.text.replace(/\d/g, "0") }
            K.Pill {
                implicitHeight: 28
                Text { text: frame.rec.mic ? "\u{F036C}" : "\u{F036D}"; color: frame.rec.mic ? K.Theme.text : K.Theme.dim; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
                onClicked: frame.rec.toggleMic()
            }
            K.Pill {
                implicitHeight: 28
                edge: K.Theme.bad
                Text { text: "\u{F04DB}"; color: K.Theme.bad; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "Stop"; color: K.Theme.text; font.pixelSize: K.Theme.fontPx; anchors.verticalCenter: parent.verticalCenter }
                onClicked: frame.rec.stop()
            }
        }
    }
}
