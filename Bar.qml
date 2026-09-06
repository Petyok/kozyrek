import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: bar
    property alias left:   leftRow.data
    property alias center: centerRow.data
    property alias right:  rightRow.data

    // Dev mode: sit at the bottom and ignore exclusive zones so the live
    // HyprPanel keeps the top edge while this one is being built.
    anchors { left: true; right: true; top: !Kozy.devMode; bottom: Kozy.devMode }
    implicitHeight: Theme.barH
    color: "transparent"
    WlrLayershell.namespace: "kozyrek"
    WlrLayershell.layer: WlrLayer.Top
    exclusionMode: Kozy.devMode ? ExclusionMode.Ignore : ExclusionMode.Auto
    Component.onCompleted: Kozy.bar = bar

    Row {
        id: leftRow
        anchors { left: parent.left; leftMargin: Theme.pad; top: parent.top; topMargin: Theme.pad }
        spacing: 6
    }
    Row {
        id: centerRow
        anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: Theme.pad }
        spacing: 6
    }
    Row {
        id: rightRow
        anchors { right: parent.right; rightMargin: Theme.pad; top: parent.top; topMargin: Theme.pad }
        spacing: 6
    }
}
