import QtQuick
import Quickshell
import Quickshell.Wayland
import Quickshell.Hyprland

// A dropdown under a bar pill. Positioning comes from PopupAnchor (no manual
// margins), click-away from HyprlandFocusGrab: a full-screen catcher window on
// the Top layer would cover the bar itself (also Top) and eat clicks on every
// other pill while a popup is open.
PopupWindow {
    id: popup
    required property Item pill
    property bool open: false
    property int contentWidth: 320
    default property alias content: col.data

    // Set on open, not as bindings: `anchor.item: pill` stays null when the
    // window is not known at construction time (verified 2026-09-07).
    anchor.edges: Edges.Bottom
    anchor.gravity: Edges.Bottom
    anchor.margins.top: 6
    anchor.adjustment: PopupAdjustment.SlideX

    visible: open && !Kozy.fsActive
    color: "transparent"
    implicitWidth: contentWidth
    implicitHeight: col.implicitHeight + 32

    onOpenChanged: {
        if (open) {
            anchor.window = Kozy.bar
            anchor.item = pill
            anchor.updateAnchor()
            if (Kozy.activePopup && Kozy.activePopup !== popup) Kozy.activePopup.open = false
            Kozy.activePopup = popup
        } else if (Kozy.activePopup === popup) {
            Kozy.activePopup = null
        }
    }

    HyprlandFocusGrab {
        windows: [popup]
        active: popup.open
        onCleared: popup.open = false
    }

    Rectangle {
        anchors.fill: parent
        radius: 18
        color: Theme.bg
        border.color: Theme.faint
        border.width: 1
        focus: popup.open
        Keys.onEscapePressed: popup.open = false
        Column {
            id: col
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
            spacing: 10
        }
    }
}
