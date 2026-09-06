import QtQuick
import Quickshell
import Quickshell.Wayland
import Quickshell.Widgets
import Quickshell.Services.Notifications

// Notification daemon + popup stack. No history, no center: the user does not
// use them. DND drops notifications on arrival.
Scope {
    id: root
    readonly property int maxShown: 5

    NotificationServer {
        id: server
        keepOnReload: true
        actionsSupported: true
        imageSupported: true
        bodyMarkupSupported: true
        onNotification: n => {
            if (Kozy.dnd) { n.dismiss(); return }
            n.tracked = true      // otherwise the object is gone right after this signal
        }
    }
    // Newest on top, at most maxShown visible; the rest wait untracked-but-alive
    // in trackedNotifications and slide in as cards expire.
    readonly property var shown: server.trackedNotifications.values.slice(-maxShown).reverse()

    function iconFor(n) {
        if (n.image) return n.image
        if (n.appIcon) return n.appIcon.startsWith("/") ? n.appIcon : Quickshell.iconPath(n.appIcon, "")
        var e = n.desktopEntry ? DesktopEntries.byId(n.desktopEntry) : null
        if (!e && n.appName) e = DesktopEntries.heuristicLookup(n.appName)
        return e?.icon ? Quickshell.iconPath(e.icon, "") : ""
    }

    PanelWindow {
        visible: root.shown.length > 0 && !Kozy.fsActive
        anchors { top: true; right: true }
        margins { top: Theme.barH + 4; right: Theme.pad }
        implicitWidth: 380
        implicitHeight: stack.implicitHeight
        color: "transparent"
        WlrLayershell.namespace: "kozyrek-notifications"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore

        Column {
            id: stack
            width: parent.width
            spacing: 8
            Repeater {
                model: ScriptModel { values: root.shown }
                Rectangle {
                    id: card
                    required property var modelData
                    readonly property var n: modelData
                    readonly property var defaultAction: n.actions.find(a => a.identifier === "default") ?? null
                    width: stack.width
                    implicitHeight: body.implicitHeight + 24
                    radius: 14
                    color: Theme.bg
                    border.width: 1
                    border.color: n.urgency === NotificationUrgency.Critical ? Theme.bad : Theme.faint

                    // Paused while hovered; critical never times out on its own.
                    // Runs only while the window is visible, so queued or
                    // fullscreen-hidden cards keep their full timeout.
                    Timer {
                        interval: card.n.expireTimeout > 0 ? card.n.expireTimeout : 6000
                        running: card.n.urgency !== NotificationUrgency.Critical && !hover.containsMouse && card.Window.window?.visible === true
                        onTriggered: card.n.expire()
                    }

                    Row {
                        id: body
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                        spacing: 10
                        IconImage {
                            id: icon
                            source: root.iconFor(card.n)
                            visible: source !== ""
                            implicitSize: 36
                        }
                        Column {
                            width: body.width - (icon.visible ? 46 : 0) - 24
                            spacing: 3
                            Text { text: card.n.summary; color: Theme.text; font.pixelSize: Theme.fontPx; font.bold: true; elide: Text.ElideRight; width: parent.width }
                            Text { visible: text !== ""; text: card.n.body; color: Theme.dim; font.pixelSize: 12; textFormat: Text.StyledText; wrapMode: Text.Wrap; maximumLineCount: 4; elide: Text.ElideRight; width: parent.width }
                            Row {
                                visible: card.n.actions.length > 0
                                spacing: 6
                                Repeater {
                                    model: card.n.actions.filter(a => a.identifier !== "default")
                                    Rectangle {
                                        height: 24; width: lbl.implicitWidth + 16; radius: 8
                                        color: bma.containsMouse ? Theme.accent : "#1effffff"; border.color: Theme.faint
                                        Text { id: lbl; anchors.centerIn: parent; text: modelData.text; color: Theme.text; font.pixelSize: 11 }
                                        MouseArea { id: bma; anchors.fill: parent; hoverEnabled: true; onClicked: { modelData.invoke(); card.n.dismiss() } }
                                    }
                                }
                            }
                        }
                        Text {
                            text: "✕"; color: Theme.dim; font.pixelSize: 12
                            MouseArea { anchors.fill: parent; onClicked: card.n.dismiss() }
                        }
                    }
                    MouseArea {
                        id: hover
                        anchors.fill: parent
                        hoverEnabled: true
                        z: -1
                        onClicked: { if (card.defaultAction) card.defaultAction.invoke(); card.n.dismiss() }
                    }
                }
            }
        }
    }
}
