import QtQuick 2.15

Canvas {
    id: icon
    property string kind: "camera"
    property color ink: "#000000"
    implicitWidth: 22
    implicitHeight: 22
    onKindChanged: requestPaint()
    onInkChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d")
        c.reset()
        c.scale(width / 24, height / 24)
        c.strokeStyle = ink
        c.fillStyle = ink
        c.lineWidth = 1.65
        c.lineCap = "round"
        c.lineJoin = "round"
        c.beginPath()
        if (kind === "camera") {
            c.moveTo(3,7); c.lineTo(7,7); c.lineTo(9,4); c.lineTo(15,4); c.lineTo(17,7); c.lineTo(21,7); c.lineTo(21,20); c.lineTo(3,20); c.closePath()
            c.moveTo(16,13); c.arc(12,13,4,0,Math.PI*2)
        } else if (kind === "photos") {
            c.rect(3,4,18,16); c.moveTo(4,18); c.lineTo(10,12); c.lineTo(14,16); c.lineTo(17,13); c.lineTo(21,17)
            c.moveTo(17,9); c.arc(15.5,9,1.5,0,Math.PI*2)
        } else if (kind === "recipes") {
            c.rect(3,3,6,6); c.rect(15,15,6,6); c.moveTo(9,6); c.lineTo(18,6); c.lineTo(18,15)
            c.moveTo(6,9); c.lineTo(6,18); c.lineTo(12,18); c.moveTo(9,15); c.lineTo(12,18); c.lineTo(9,21)
        } else if (kind === "settings") {
            c.moveTo(3,6); c.lineTo(21,6); c.moveTo(3,12); c.lineTo(21,12); c.moveTo(3,18); c.lineTo(21,18)
            c.moveTo(8,3); c.lineTo(8,9); c.moveTo(16,9); c.lineTo(16,15); c.moveTo(10,15); c.lineTo(10,21)
        } else if (kind === "devices") {
            c.rect(6,6,12,12); c.rect(9,9,6,6)
            for (var i=8;i<=16;i+=4) { c.moveTo(i,3); c.lineTo(i,6); c.moveTo(i,18); c.lineTo(i,21); c.moveTo(3,i); c.lineTo(6,i); c.moveTo(18,i); c.lineTo(21,i) }
        } else if (kind === "diagnostics") {
            c.moveTo(2,13); c.lineTo(6,13); c.lineTo(9,5); c.lineTo(13,20); c.lineTo(16,10); c.lineTo(18,13); c.lineTo(22,13)
        } else if (kind === "system") {
            c.rect(3,4,18,13); c.moveTo(8,21); c.lineTo(16,21); c.moveTo(12,17); c.lineTo(12,21)
        } else if (kind === "search") {
            c.arc(10,10,6,0,Math.PI*2); c.moveTo(15,15); c.lineTo(21,21)
        } else if (kind === "menu") {
            c.moveTo(4,6); c.lineTo(20,6); c.moveTo(4,12); c.lineTo(20,12); c.moveTo(4,18); c.lineTo(20,18)
        } else if (kind === "back") {
            c.moveTo(14,5); c.lineTo(7,12); c.lineTo(14,19); c.moveTo(7,12); c.lineTo(21,12)
        } else if (kind === "apps") {
            c.rect(4,4,6,6); c.rect(14,4,6,6); c.rect(4,14,6,6); c.rect(14,14,6,6)
        } else {
            c.arc(12,12,8,0,Math.PI*2); c.moveTo(12,10); c.lineTo(12,17)
        }
        c.stroke()
    }
}
