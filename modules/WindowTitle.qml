import QtQuick
import Quickshell.Hyprland
import "../" as K

// The one pill that gives way: it elides into whatever the left zone has left
// after the other zones, and hides when that is too little to read.
K.Pill {
    id: pill
    // Left-zone siblings and their gaps; not this pill, so no binding loop.
    readonly property real others: parent ? Array.prototype.reduce.call(parent.children, (s, c) => c === pill || !c.visible ? s : s + c.width + parent.spacing, 0) : 0
    readonly property real room: Math.min(400, (K.Kozy.bar?.leftRoom ?? 400) - others - 24)
    visible: text.text !== "" && room >= 80
    Text {
        id: text
        text: Hyprland.activeToplevel?.title ?? ""
        color: K.Theme.dim
        font.pixelSize: K.Theme.fontPx
        elide: Text.ElideRight
        width: Math.min(implicitWidth, Math.max(0, pill.room))
        anchors.verticalCenter: parent.verticalCenter
    }
}
