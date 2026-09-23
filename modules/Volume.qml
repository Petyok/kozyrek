import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Services.Pipewire
import "../" as K
import "../lib/parse.js" as Parse

// Default sink: icon + percent. Click opens the slider, right click toggles
// mute, scroll steps by 5 %. Same 0..150 % range as the OSD.
K.Pill {
    id: root
    readonly property var sink: Pipewire.defaultAudioSink
    readonly property var audio: sink?.audio ?? null
    readonly property bool muted: audio?.muted ?? false
    readonly property real vol: audio?.volume ?? 0
    visible: audio !== null
    active: menu.open

    PwObjectTracker { objects: [root.sink] }

    function icon() { return muted ? "\u{F0581}" : vol < 0.34 ? "\u{F057F}" : vol < 0.67 ? "\u{F0580}" : "\u{F057E}" }
    function toggleMute() { if (audio) audio.muted = !audio.muted }

    onClicked: button => button === Qt.RightButton ? toggleMute() : menu.open = !menu.open
    onWheeled: delta => { if (audio) audio.volume = Parse.stepVolume(audio.volume, delta > 0 ? "+5" : "-5", 1.5) }

    Text { text: root.icon(); color: root.muted ? K.Theme.dim : K.Theme.text; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
    Text { text: Math.round(root.vol * 100) + "%"; color: root.muted ? K.Theme.dim : K.Theme.text; font.pixelSize: K.Theme.fontPx; anchors.verticalCenter: parent.verticalCenter }

    K.Popup {
        id: menu
        pill: root
        contentWidth: 300
        readonly property int colW: contentWidth - 32

        Row {
            width: menu.colW
            Text { text: "Sound"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; anchors.verticalCenter: parent.verticalCenter; width: menu.colW - 60 }
            Switch { checked: !root.muted; onToggled: root.audio.muted = !checked }
        }
        Text { text: root.sink?.description ?? ""; color: K.Theme.dim; font.pixelSize: 12; elide: Text.ElideRight; width: menu.colW }
        Row {
            width: menu.colW
            spacing: 8
            Text {
                text: root.icon(); color: root.muted ? K.Theme.dim : K.Theme.text; font.pixelSize: 18; font.family: K.Theme.iconFont
                anchors.verticalCenter: parent.verticalCenter
                MouseArea { anchors.fill: parent; onClicked: root.toggleMute() }
            }
            Slider {
                from: 0; to: 1.5; stepSize: 0.01
                value: root.vol
                onMoved: if (root.audio) root.audio.volume = value
                width: menu.colW - 80
                anchors.verticalCenter: parent.verticalCenter
            }
            Text { text: Math.round(root.vol * 100) + "%"; color: root.vol > 1 ? K.Theme.warn : K.Theme.text; font.pixelSize: K.Theme.fontPx; font.bold: true; width: 40; horizontalAlignment: Text.AlignRight; anchors.verticalCenter: parent.verticalCenter }
        }
    }
}
