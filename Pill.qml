import QtQuick

// The one button shape every module uses, so the bar reads as one thing.
Rectangle {
    id: pill
    property bool active: false
    readonly property bool hovered: ma.containsMouse
    default property alias content: inner.data
    signal clicked(int button)

    implicitWidth: inner.implicitWidth + 24
    implicitHeight: Theme.pillH
    radius: Theme.radius
    color: active ? Theme.accent : (hovered ? "#33ffffff" : "#1effffff")
    border.width: 1
    border.color: active ? "transparent" : Theme.faint
    Behavior on color { ColorAnimation { duration: 120 } }

    Row {
        id: inner
        anchors.centerIn: parent
        spacing: 7
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        onClicked: mouse => pill.clicked(mouse.button)
    }
}
