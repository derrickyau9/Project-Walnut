import QtQuick 2.15
import QtQuick.Controls 2.15

// Translate the visible label, while currentText/model IDs remain unchanged.
ComboBox {
    id: control
    displayText: qsTr(currentText)
    delegate: ItemDelegate {
        width: control.width
        text: qsTr(control.textRole ? String(modelData[control.textRole]) : String(modelData))
        highlighted: control.highlightedIndex === index
        font: control.font
    }
}
