import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Widgets
import "../" as K
import "../lib/parse.js" as Parse

// One pill for all workspaces (HyprPanel look): app icons per workspace, the
// focused one gets an accent underline, empty ones show their number.
K.Pill {
    id: pill
    Repeater {
        model: ScriptModel { values: Parse.wsSort(Hyprland.workspaces.values) }
        Item {
            id: ws
            required property var modelData
            readonly property bool empty: modelData.toplevels.values.length === 0
            width: Math.max(18, icons.implicitWidth)
            height: K.Theme.pillH
            Row {
                id: icons
                anchors.centerIn: parent
                spacing: 4
                Repeater {
                    model: ws.modelData.toplevels
                    Item {
                        required property var modelData
                        // Class from the IPC object (always present), appId from the
                        // wayland handle as a fallback (may be null before it binds).
                        readonly property string cls: modelData.lastIpcObject?.class ?? modelData.wayland?.appId ?? ""
                        readonly property var entry: cls ? DesktopEntries.heuristicLookup(cls) : null
                        readonly property bool hasIcon: (entry?.icon ?? "") !== "" && Quickshell.hasThemeIcon(entry.icon)
                        width: 18; height: 18
                        IconImage { anchors.fill: parent; visible: parent.hasIcon; source: parent.hasIcon ? Quickshell.iconPath(parent.entry.icon) : "" }
                        // No desktop entry / no themed icon: a generic window glyph.
                        Text { anchors.centerIn: parent; visible: !parent.hasIcon; text: "\u{F05AF}"; color: K.Theme.text; font.pixelSize: 15; font.family: K.Theme.iconFont }
                    }
                }
                Text {
                    visible: ws.empty
                    text: ws.modelData.id
                    color: ws.modelData.focused ? K.Theme.text : K.Theme.dim
                    font.pixelSize: K.Theme.fontPx; font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Rectangle {
                visible: ws.modelData.focused
                anchors { bottom: parent.bottom; bottomMargin: 5; horizontalCenter: parent.horizontalCenter }
                width: Math.max(14, icons.implicitWidth); height: 2; radius: 1
                color: K.Theme.accent
            }
            MouseArea { anchors.fill: parent; onClicked: ws.modelData.activate() }
        }
    }
}
