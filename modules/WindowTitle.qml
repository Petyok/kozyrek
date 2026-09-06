import QtQuick
import Quickshell.Hyprland
import "../" as K

K.Pill {
    visible: text.text !== ""
    Text {
        id: text
        text: Hyprland.activeToplevel?.title ?? ""
        color: K.Theme.dim
        font.pixelSize: K.Theme.fontPx
        elide: Text.ElideRight
        width: Math.min(implicitWidth, 400)
        anchors.verticalCenter: parent.verticalCenter
    }
}
