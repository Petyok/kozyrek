import QtQuick
import Quickshell
import Quickshell.Services.SystemTray
import Quickshell.Widgets
import "../" as K

Row {
    spacing: 4
    Repeater {
        model: SystemTray.items
        K.Pill {
            id: pill
            required property var modelData
            // SystemTrayItem.icon is already an image source string.
            IconImage { source: pill.modelData.icon; implicitSize: 18; anchors.verticalCenter: parent.verticalCenter }
            // nm-applet is `onlyMenu`: left click must open the menu too.
            onClicked: button => {
                if (button === Qt.RightButton || pill.modelData.onlyMenu) { if (pill.modelData.hasMenu) menu.open() }
                else if (button === Qt.MiddleButton) pill.modelData.secondaryActivate()
                else pill.modelData.activate()
            }
            QsMenuAnchor {
                id: menu
                menu: pill.modelData.menu
                anchor.window: pill.QsWindow.window
                anchor.item: pill
                anchor.edges: Edges.Bottom
                anchor.gravity: Edges.Bottom
                anchor.margins.top: 6
            }
        }
    }
}
