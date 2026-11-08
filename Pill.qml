import QtQuick

// The one button shape every module uses, so the bar reads as one thing.
Rectangle {
    id: pill
    property bool active: false
    readonly property bool hovered: ma.containsMouse
    property real progress: -1          // 0..1 draws a fill behind the content; < 0 = none
    property bool loud: false           // has something to say: an overflow item steps out onto the bar
    property color edge: Theme.faint    // border colour while not active
    default property alias content: inner.data
    signal clicked(int button)
    signal wheeled(int delta)   // angleDelta.y, +120 per notch up

    implicitWidth: inner.implicitWidth + 24
    implicitHeight: Theme.pillH
    radius: Theme.radius
    color: active ? Theme.accent : (hovered ? "#33ffffff" : "#1effffff")
    border.width: 1
    border.color: active ? "transparent" : edge
    Behavior on color { ColorAnimation { duration: 120 } }

    // Declared before `inner` so it paints under the content. A rectangular clip
    // over a full-size rounded child: the left edge stays round, the right edge is
    // cut straight, and a 2 % fill is a sliver instead of a rounded blob.
    Item {
        visible: pill.progress >= 0
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        width: pill.width * Math.max(0, Math.min(1, pill.progress))
        clip: true
        Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
        Rectangle { x: 1; y: 1; width: pill.width - 2; height: pill.height - 2; radius: pill.radius - 1; color: "#4db69ad6" }
    }
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
        onWheel: wheel => pill.wheeled(wheel.angleDelta.y)
    }
}
