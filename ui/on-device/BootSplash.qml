import QtQuick 2.15
Rectangle {
    id:splash
    width:800
    height:480
    color:"black"
    property real progress:typeof bootStatus !== "undefined" ? bootStatus.progress : 0.7
    property string stage:typeof bootStatus !== "undefined" ? bootStatus.stage : "Starting DaOS"
    property real pulse:0
    Image {
        anchors.centerIn:parent
        width:Math.min(590,parent.width-40)
        height:Math.min(440,parent.height-40)
        source:Qt.platform.os === "windows" ? "../../assets/boot-logo/DaOS-logo.png" : "file:///boot/images/DaOS-logo.png"
        fillMode:Image.PreserveAspectFit
        smooth:true
    }
    Text { x:12;y:6;text:splash.stage;color:"#cccccc";font.pixelSize:11 }
    Rectangle {
        id:track
        x:parent.width/8
        y:parent.height-12
        width:parent.width*3/4
        height:4
        color:"#333333"
        clip:true
        Rectangle { width:track.width*splash.progress;height:parent.height;color:"#aaaaaa" }
        Rectangle { x:(track.width+100)*splash.pulse-100;width:100;height:parent.height;color:"#ffffff";opacity:.75 }
    }
    NumberAnimation on pulse { from:0;to:1;duration:1600;loops:Animation.Infinite;running:splash.visible }
}
