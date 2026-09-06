pragma Singleton
import QtQuick

QtObject {
    readonly property color bg:     "#e6141826"
    readonly property color accent: "#b69ad6"
    readonly property color text:   "#eef1fa"
    readonly property color dim:    "#a6b6c4d8"
    readonly property color faint:  "#33ffffff"
    readonly property color today:  "#705492"
    readonly property color warn:   "#e8b34a"
    readonly property color bad:    "#e05561"

    readonly property int barH:   51
    readonly property int pillH:  34
    readonly property int pad:    8
    readonly property int radius: 12
    readonly property int fontPx: 13
    readonly property string iconFont: "Symbols Nerd Font"
}
