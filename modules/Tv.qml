import QtQuick
import "../" as K

// TV remote button: lit while the overlay is open. The overlay itself is ../Tv.qml.
K.Pill {
    id: pill
    property bool on: false
    active: on
    Text { text: "\u{F0502}"; color: pill.on ? "white" : K.Theme.dim; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
}
