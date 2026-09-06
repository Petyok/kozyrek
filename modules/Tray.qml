import QtQuick
import Quickshell
import Quickshell.Services.SystemTray
import Quickshell.Widgets
import "../" as K

// Tray items with their DBus menus drawn by us (QsMenuOpener exposes entries as
// data). QsMenuAnchor would hand the menu to Qt's native platform menu: white,
// unthemed, and positioned at the screen corner under Hyprland.
Row {
    spacing: 4
    Repeater {
        model: SystemTray.items
        K.Pill {
            id: pill
            required property var modelData
            active: menu.open
            // SystemTrayItem.icon is already an image source string.
            IconImage { source: pill.modelData.icon; implicitSize: 18; anchors.verticalCenter: parent.verticalCenter }
            // Left and right click both open the menu (user preference); middle click
            // is the SNI secondary action. Items without a menu get activate() on left.
            onClicked: button => {
                if (button === Qt.MiddleButton) pill.modelData.secondaryActivate()
                else if (pill.modelData.hasMenu) menu.open = !menu.open
                else pill.modelData.activate()
            }

            K.Popup {
                id: menu
                pill: pill
                contentWidth: 260
                readonly property int colW: contentWidth - 32
                // Drill-down: `stack` holds parent handles, `handle` the level shown.
                property var handle: pill.modelData.menu
                property var stack: []
                onOpenChanged: if (open) { handle = pill.modelData.menu; stack = [] }
                QsMenuOpener { id: opener; menu: menu.handle }

                Rectangle {
                    visible: menu.stack.length > 0
                    width: menu.colW; height: 28; radius: 8
                    color: backMa.containsMouse ? "#33ffffff" : "transparent"
                    Text { anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter } text: "‹  back"; color: K.Theme.dim; font.pixelSize: 12 }
                    MouseArea {
                        id: backMa; anchors.fill: parent; hoverEnabled: true
                        onClicked: {
                            var s = menu.stack.slice()
                            menu.handle = s.pop()
                            menu.stack = s
                        }
                    }
                }
                Repeater {
                    model: opener.children
                    Item {
                        id: row
                        required property var modelData
                        width: menu.colW
                        height: modelData.isSeparator ? 9 : 30
                        Rectangle { visible: row.modelData.isSeparator; anchors.centerIn: parent; width: parent.width; height: 1; color: K.Theme.faint }
                        Rectangle {
                            visible: !row.modelData.isSeparator
                            anchors.fill: parent; radius: 8
                            color: ma.containsMouse && row.modelData.enabled ? "#33ffffff" : "transparent"
                            opacity: row.modelData.enabled ? 1 : 0.4
                            Row {
                                anchors { left: parent.left; leftMargin: 8; right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
                                spacing: 8
                                Text {
                                    width: 14
                                    text: row.modelData.checkState === Qt.Checked ? "✓" : ""
                                    color: K.Theme.accent; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
                                }
                                IconImage { visible: row.modelData.icon !== ""; source: row.modelData.icon; implicitSize: 16; anchors.verticalCenter: parent.verticalCenter }
                                Text {
                                    text: row.modelData.text
                                    color: K.Theme.text; font.pixelSize: K.Theme.fontPx; elide: Text.ElideRight
                                    width: parent.width - 14 - (row.modelData.icon !== "" ? 24 : 0) - 24
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Text { text: row.modelData.hasChildren ? "›" : ""; color: K.Theme.dim; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
                            }
                            MouseArea {
                                id: ma; anchors.fill: parent; hoverEnabled: true
                                enabled: row.modelData.enabled
                                onClicked: {
                                    if (row.modelData.hasChildren) { menu.stack = menu.stack.concat([menu.handle]); menu.handle = row.modelData }
                                    else { row.modelData.triggered(); menu.open = false }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
