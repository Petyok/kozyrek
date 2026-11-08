import QtQuick
import "../" as K

// "Show hidden icons", Windows 7 style: `items` live in this pill's popup instead
// of on the bar, and each one steps back to its own place on the bar while its
// `loud` is true (recording, agent working, a question waiting...). A pill opened
// from the popup anchors its own popup to this chevron (see Popup.qml).
K.Pill {
    id: root
    property list<Item> items
    property Item row: null               // the bar row the items belong to
    property var home: []                 // its children in declared order
    // Not `visible`: that is the effective visibility, false for everything in the
    // popup once it has been closed. An empty tray row is 0 wide.
    readonly property int hiddenCount: Array.prototype.filter.call(items, it => it.parent === grid && it.width > 0).length

    visible: hiddenCount > 0
    active: pop.open
    onClicked: pop.open = !pop.open
    Component.onCompleted: {
        K.Kozy.overflow = root
        row = parent
        home = Array.prototype.slice.call(row.children)
        for (const it of Array.prototype.slice.call(items)) {
            it.loudChanged?.connect(() => place(it))
            it.clicked?.connect(() => pop.open = false)
            place(it)
        }
    }

    function holds(item) {
        for (let p = item; p; p = p.parent) if (p === grid) return true
        return false
    }
    // Reparenting appends at the end, and QQuickItem::stackAfter is not exposed to
    // QML, so the containers are re-appended in declared order afterwards, by way of
    // `shelf` (a null parent breaks `anchors…parent` bindings). Not this pill: an item
    // that reparents itself stops painting, so it must be declared first in its row.
    function place(it) {
        const want = it.loud ? row : grid
        if (it.parent === want) return
        it.parent = want
        for (const box of [row, grid])
            for (const k of home.filter(k => k.parent === box && k !== root)) { k.parent = shelf; k.parent = box }
    }
    Item { id: shelf; visible: false }

    Text {
        text: "\u{F0140}"                                  // chevron down
        color: pop.open ? "white" : K.Theme.dim
        font.pixelSize: 20; font.family: K.Theme.iconFont
        anchors.verticalCenter: parent.verticalCenter
    }

    K.Popup {
        id: pop
        pill: root
        contentWidth: Math.max(120, grid.implicitWidth + 32)
        Row { id: grid; spacing: 6 }
    }
}
