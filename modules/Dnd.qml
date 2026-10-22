import QtQuick
import "../" as K

// Only while on (turned on with Super+, / `ipc call dnd toggle`); click turns it off.
K.Pill {
    visible: K.Kozy.dnd
    active: K.Kozy.dnd
    Text { text: K.Kozy.dnd ? "\u{F009B}" : "\u{F009A}"; color: K.Kozy.dnd ? "white" : K.Theme.dim; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
    onClicked: K.Kozy.dnd = !K.Kozy.dnd
}
