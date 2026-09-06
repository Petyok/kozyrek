import QtQuick
import Quickshell
import "../" as K

K.Pill {
    id: pill
    active: menu.open
    Text { text: "⏻"; color: K.Theme.text; font.pixelSize: 15; font.family: K.Theme.iconFont }
    onClicked: menu.open = !menu.open

    K.Popup {
        id: menu
        pill: pill
        contentWidth: 200
        Repeater {
            model: [
                { t: "Lock",     i: "\u{F033E}", cmd: ["loginctl", "lock-session"] },
                { t: "Suspend",  i: "\u{F04B2}", cmd: ["systemctl", "suspend"] },
                { t: "Reboot",   i: "\u{F0709}", cmd: ["systemctl", "reboot"] },
                { t: "Poweroff", i: "\u{F0425}", cmd: ["systemctl", "poweroff"] },
            ]
            Rectangle {
                width: parent.width; height: 36; radius: 10
                color: ma.containsMouse ? "#33ffffff" : "transparent"
                Row {
                    anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                    spacing: 10
                    Text { text: modelData.i; color: modelData.t === "Poweroff" ? K.Theme.bad : K.Theme.text; font.pixelSize: 16; font.family: K.Theme.iconFont }
                    Text { text: modelData.t; color: K.Theme.text; font.pixelSize: K.Theme.fontPx }
                }
                MouseArea {
                    id: ma; anchors.fill: parent; hoverEnabled: true
                    onClicked: { menu.open = false; Quickshell.execDetached(modelData.cmd) }
                }
            }
        }
    }
}
