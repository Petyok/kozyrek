import QtQuick
import "../" as K

K.Pill {
    active: K.Kozy.dnd
    Text { text: K.Kozy.dnd ? "\u{F009B}" : "\u{F009A}"; color: K.Kozy.dnd ? "white" : K.Theme.dim; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
    onClicked: K.Kozy.dnd = !K.Kozy.dnd
}
