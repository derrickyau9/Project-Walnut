import QtQuick 2.15
import QtQuick.Controls 2.15

Button {
    id: control
    property bool emphasized: false
    implicitWidth: 88
    implicitHeight: 64
    padding: 8
    font.family: typeof cameraUIFont !== "undefined" ? cameraUIFont : "Noto Sans CJK SC"
    font.pixelSize: 18
    font.weight: emphasized ? Font.Medium : Font.Normal
    font.letterSpacing: 0.25
    contentItem: Text {
        text: control.text
        textFormat: Text.PlainText
        font: control.font
        color: control.enabled ? "#ffffff" : "#737373"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
        maximumLineCount: 2
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: control.down ? "#555555" : control.emphasized ? "#3d3d3d" : "#191919"
        border.color: control.activeFocus || control.emphasized ? "#dddddd" : "#353535"
        border.width: 1
        radius: 6
    }
}
