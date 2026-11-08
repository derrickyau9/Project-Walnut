import QtQuick 2.15

Item {
    id:readout
    property string value:""
    property string family:typeof cameraUIFont !== "undefined" ? cameraUIFont : "Noto Sans CJK SC"
    property int pixelSize:28
    property color ink:"#ffffff"
    property real tracking:.25
    property bool reduceMotion:false
    property bool initialized:false
    property string activityName:""
    property real arrival:1
    property real offset:0
    implicitWidth:label.implicitWidth
    implicitHeight:label.implicitHeight
    clip:true
    onValueChanged:if(initialized && !reduceMotion) {arrival=.7;offset=4;settle.restart()}
    Component.onCompleted:initialized=true
    onReduceMotionChanged:if(reduceMotion) {settle.stop();arrival=1;offset=0}
    Text {
        id:label
        anchors.fill:parent
        text:readout.value
        font.family:readout.family;font.pixelSize:readout.pixelSize;font.weight:Font.Normal;font.letterSpacing:readout.tracking
        color:readout.ink
        horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter
        opacity:readout.arrival
        transform:Translate {y:readout.offset}
    }
    ParallelAnimation {
        id:settle
        onRunningChanged:if(readout.activityName && typeof uiPerformance !== "undefined") uiPerformance.setActivity(readout.activityName,running)
        NumberAnimation {target:readout;property:"arrival";to:1;duration:readout.reduceMotion ? 0 : 220;easing.type:Easing.OutCubic}
        NumberAnimation {target:readout;property:"offset";to:0;duration:readout.reduceMotion ? 0 : 220;easing.type:Easing.OutCubic}
    }
}
