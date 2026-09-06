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
                        // DesktopEntries scans asynchronously (empty for ~1 s after start); depend on
                        // the list so the lookup re-runs once it is populated.
                        readonly property int appsSeen: DesktopEntries.applications.values.length
                        readonly property var entry: (appsSeen >= 0 && cls) ? DesktopEntries.heuristicLookup(cls) : null
                        readonly property bool hasIcon: (entry?.icon ?? "") !== "" && Quickshell.hasThemeIcon(entry.icon)
                        width: 18; height: 18
                        IconImage { anchors.fill: parent; visible: parent.hasIcon; source: parent.hasIcon ? Quickshell.iconPath(parent.entry.icon) : "" }
                        // No desktop entry / no themed icon: a generic window glyph.
                        Text { anchors.centerIn: parent; visible: !parent.hasIcon; text: "\u{F05AF}"; color: K.Theme.text; font.pixelSize: 15; font.family: K.Theme.iconFont }
                    }
                }
                // Empty workspace: a dot, not the id — Hyprland ids here are arbitrary
                // (15…20) and mean nothing to the user.
                Rectangle {
                    visible: ws.empty
                    width: 8; height: 8; radius: 4
                    color: ws.modelData.focused ? K.Theme.text : K.Theme.dim
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
