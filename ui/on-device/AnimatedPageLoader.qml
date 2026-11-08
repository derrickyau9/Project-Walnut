import QtQuick 2.15

Item {
    id:pages
    property Component sourceComponent
    property bool reduceMotion:false
    property var cache:[]
    property var currentItem:null
    readonly property var item:currentItem
    property bool initialized:false
    property real arrival:1
    property real offset:0
    function showPage() {
        if(!initialized) return
        if(currentItem) currentItem.visible=false
        currentItem=null
        if(!sourceComponent) return
        for(var i=0;i<cache.length;i++) if(cache[i].component === sourceComponent) {currentItem=cache[i].item;break}
        if(!currentItem) {
            currentItem=sourceComponent.createObject(viewport)
            if(!currentItem) return
            currentItem.width=Qt.binding(function(){return viewport.width})
            currentItem.height=Qt.binding(function(){return viewport.height})
            cache.push({component:sourceComponent,item:currentItem})
        }
        currentItem.visible=true
        if(!reduceMotion) {arrival=.75;offset=8;motion.restart()} else {arrival=1;offset=0}
    }
    onSourceComponentChanged:showPage()
    onReduceMotionChanged:if(reduceMotion) {motion.stop();arrival=1;offset=0}
    Component.onCompleted:{initialized=true;showPage()}
    Item {
        id:viewport
        anchors.fill:parent
        clip:true
        opacity:pages.arrival
        transform:Translate {x:pages.offset}
    }
    ParallelAnimation {
        id:motion
        onRunningChanged:if(typeof uiPerformance !== "undefined") uiPerformance.setActivity("menu-transition",running)
        NumberAnimation {target:pages;property:"arrival";to:1;duration:pages.reduceMotion ? 0 : 220;easing.type:Easing.OutCubic}
        NumberAnimation {target:pages;property:"offset";to:0;duration:pages.reduceMotion ? 0 : 280;easing.type:Easing.OutCubic}
    }
}
