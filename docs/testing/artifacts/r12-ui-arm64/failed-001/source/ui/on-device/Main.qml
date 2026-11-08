import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: body
    objectName: "cameraFirstWindow"
    visible: true
    width: 800
    height: 600
    minimumWidth: 800
    minimumHeight: 600
    title: "DaOS Camera / FF-1"
    color: "#080a0d"
    font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
    font.pixelSize: 14
    readonly property var menuTheme: cameraShell.theme === "dark" ? retailTheme.dark : retailTheme.light
    readonly property string currentScreen: cameraShell.currentApp === "camera" ? "capture" : cameraShell.currentApp === "settings" ? "menu" : cameraShell.currentApp === "photos" ? "playback" : "tool"
    readonly property bool capturing: ["submitting", "accepted", "capturing"].indexOf(camera.captureState) >= 0
    readonly property bool uncertain: camera.captureState === "unknown" || camera.connectionState === "unknown"
    readonly property bool settingsReady: camera.connectionState === "ready" && camera.canCapture && !capturing
    readonly property var speeds: [
        {label:"30 s",us:30000000},{label:"15 s",us:15000000},{label:"8 s",us:8000000},
        {label:"4 s",us:4000000},{label:"2 s",us:2000000},{label:"1 s",us:1000000},
        {label:"1/2",us:500000},{label:"1/4",us:250000},{label:"1/8",us:125000},
        {label:"1/15",us:66667},{label:"1/30",us:33333},{label:"1/60",us:16667},
        {label:"1/100",us:10000},{label:"1/125",us:8000},{label:"1/250",us:4000},
        {label:"1/500",us:2000},{label:"1/1000",us:1000},{label:"1/2000",us:500},
        {label:"1/4000",us:250},{label:"1/8000",us:125},{label:"1/10000",us:100}
    ]
    property string quickField: "exposure"
    property string toastRequest: ""
    property bool savedToast: false
    property bool liveViewDesired: true

    function shutterLabel(us) {
        if (us >= 1000000) return (us/1000000).toFixed(us % 1000000 ? 1 : 0) + " s"
        return "1/" + Math.round(1000000/us)
    }
    function speedIndex(us) {
        var best=0, distance=Infinity
        for (var i=0;i<speeds.length;i++) {
            var d=Math.abs(Math.log(speeds[i].us/us))
            if(d<distance) {best=i;distance=d}
        }
        return best
    }
    function openQuick(field) {
        quickField=field
        quickControls.open()
    }
    function shoot() {
        if(currentScreen === "capture" && !quickControls.visible && !acknowledgeDialog.visible && camera.canCapture)
            camera.shutter()
    }
    function returnToCamera() {
        quickControls.close()
        cameraShell.navigate("camera")
    }
    onCurrentScreenChanged: {
        if(currentScreen !== "capture" && camera.previewActive) camera.togglePreview()
        if(currentScreen === "tool") { cameraApps.refreshDiagnostics(); cameraApps.refreshRecipes() }
    }
    onClosing: camera.shutdown()
    palette.window: menuTheme.background
    palette.windowText: menuTheme.text
    palette.base: menuTheme.raised
    palette.text: menuTheme.text
    palette.button: menuTheme.layer
    palette.buttonText: menuTheme.text
    palette.highlight: menuTheme.accent
    palette.highlightedText: cameraShell.theme === "dark" ? "#151a22" : "#ffffff"
    palette.dark: menuTheme.muted

    Shortcut { sequence: "Space"; autoRepeat: false; enabled: body.currentScreen === "capture" && !quickControls.visible && !acknowledgeDialog.visible && camera.canCapture; onActivated: body.shoot() }
    Shortcut { sequence: "Q"; autoRepeat: false; enabled: body.currentScreen === "capture" && !quickControls.visible && !acknowledgeDialog.visible; onActivated: body.openQuick("exposure") }
    Shortcut { sequence: "M"; autoRepeat: false; enabled: body.currentScreen === "capture" && !quickControls.visible && !acknowledgeDialog.visible; onActivated: cameraShell.navigate("settings") }
    Shortcut { sequence: "P"; autoRepeat: false; enabled: body.currentScreen === "capture" && !quickControls.visible && !acknowledgeDialog.visible; onActivated: cameraShell.navigate("photos") }
    Shortcut {
        sequence: "Escape"
        autoRepeat: false
        enabled: !quickControls.visible && !acknowledgeDialog.visible
        onActivated: {
            if(body.currentScreen === "playback" && playbackScene.closeOverlay()) return
            if(body.currentScreen === "tool") cameraShell.back()
            else if(body.currentScreen !== "capture") body.returnToCamera()
        }
    }

    Timer {
        id: liveStart
        objectName: "autoPreviewTimer"
        interval: 250
        repeat: true
        running: body.currentScreen === "capture" && body.liveViewDesired
        onTriggered: {
            if(camera.connectionState === "ready" && camera.canCapture && !camera.previewActive && !body.uncertain)
                camera.togglePreview()
        }
    }
    Timer { id: savedTimer; interval: 2200; onTriggered: body.savedToast=false }
    Connections {
        target: camera
        function onChanged() {
            if(camera.captureState === "complete" && camera.lastRequestId.length && body.toastRequest !== camera.lastRequestId) {
                body.toastRequest=camera.lastRequestId
                body.savedToast=true
                savedTimer.restart()
            }
        }
    }

    Item {
        id: shooting
        anchors.fill: parent
        visible: body.currentScreen === "capture"
        Rectangle { anchors.fill: parent; color: "#050709" }
        Item {
            id: pictureArea
            objectName: "viewfinderArea"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: exposureBar.top
            Image {
                id: liveImage
                objectName: "viewfinderImage"
                anchors.fill: parent
                source: camera.previewUrl
                fillMode: Image.PreserveAspectFit
                smooth: true
                cache: false
                opacity: cameraShell.previewBrightness/100.0
                visible: camera.previewActive && source.toString().length > 0 && status !== Image.Error
                Accessible.name: "HF-Sim live view, independently synthesized preview"
            }
            Item {
                anchors.centerIn: parent
                width: liveImage.paintedWidth > 0 ? liveImage.paintedWidth : parent.width
                height: liveImage.paintedHeight > 0 ? liveImage.paintedHeight : parent.height
                visible: cameraShell.gridEnabled && liveImage.visible
                Repeater { model:2; Rectangle { x:(index+1)*parent.width/3; width:1; height:parent.height; color:"white"; opacity:.3 } }
                Repeater { model:2; Rectangle { y:(index+1)*parent.height/3; width:parent.width; height:1; color:"white"; opacity:.3 } }
            }
            Text {
                anchors.centerIn: parent
                visible: !liveImage.visible && !body.uncertain
                text: body.capturing ? "Saving RAW…" : camera.connectionState === "connecting" ? "Starting camera…" : camera.connectionState === "ready" ? "Live view paused" : "Camera unavailable"
                color: "#d6dbe2"
                font.family: body.font.family
                font.pixelSize: 17
            }
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: 12
                width: topLeft.implicitWidth+20
                height: 32
                color: "#90080a0d"
                radius: 3
                Text { id:topLeft; anchors.centerIn:parent; text:"M   RAW 14"; color:"white"; font.family:body.font.family; font.pixelSize:14; font.weight:Font.DemiBold; font.letterSpacing:.8 }
            }
            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 12
                width: 72
                height: 28
                color: "#90080a0d"
                radius: 3
                Text { anchors.centerIn:parent; text:"HF-Sim"; color:"#cbd1d9"; font.family:body.font.family; font.pixelSize:11 }
            }
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 18
                width: saveText.implicitWidth+28
                height: 32
                radius: 4
                color: "#c0141a22"
                visible: body.savedToast || body.capturing
                Text { id:saveText; anchors.centerIn:parent; text:body.capturing ? "Saving…" : "RAW saved"; color:body.capturing ? "#f3f5fa" : "#b8efcb"; font.family:body.font.family; font.pixelSize:13 }
            }
            Button {
                id: touchShutter
                objectName: "shootButton"
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 20
                width: 64; height: 64
                autoRepeat: false
                enabled: camera.canCapture && !quickControls.visible && !body.uncertain
                onClicked: body.shoot()
                Accessible.name: "Take one photograph"
                background: Rectangle {
                    radius: 32
                    color: "#90101419"
                    border.width: 2
                    border.color: touchShutter.enabled ? "#f3f5fa" : "#66707e"
                    Rectangle { anchors.centerIn:parent; width:46; height:46; radius:23; color:touchShutter.down ? "#d74640" : touchShutter.enabled ? "#f3f5fa" : "#58616f" }
                }
            }
            Rectangle {
                objectName: "captureRecoveryPanel"
                visible: body.uncertain
                anchors.centerIn: parent
                width: Math.min(parent.width-48,620)
                height: recoveryBody.implicitHeight+36
                radius: 8
                color: "#ed171b22"
                border.color: "#b89458"
                ColumnLayout {
                    id: recoveryBody
                    anchors.fill:parent
                    anchors.margins:18
                    spacing:12
                    Label { text:"Capture status unconfirmed"; color:"#f1d298"; font.pixelSize:19; font.weight:Font.DemiBold }
                    Label { Layout.fillWidth:true; text:"Check the previous photograph before taking another."; color:"#e1e5ec"; wrapMode:Text.WordWrap; font.pixelSize:13 }
                    RowLayout {
                        Layout.fillWidth:true
                        Button { objectName:"queryOriginalButton"; text:"Check capture"; enabled:camera.lastRequestId.length>0 && !camera.busy; onClicked:camera.queryLast(); implicitHeight:44 }
                        Button { objectName:"reconnectButton"; text:"Reconnect"; enabled:!camera.busy; onClicked:camera.reconnect(); implicitHeight:44 }
                        Button { objectName:"acknowledgeUnknownButton"; text:"New shot…"; enabled:camera.canAcknowledgeUnknown; onClicked:acknowledgeDialog.open(); implicitHeight:44 }
                    }
                }
            }
        }
        Rectangle {
            id: exposureBar
            objectName: "shootingStatusBar"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 66
            color: "#12171e"
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin:10
                anchors.rightMargin:10
                spacing: 4
                Button { objectName:"menuButton"; text:"MENU"; flat:true; Layout.preferredWidth:78; Layout.fillHeight:true; font.pixelSize:13; palette.buttonText:"#e6e9ef"; onClicked:cameraShell.navigate("settings"); Accessible.name:"Camera menu" }
                Button { objectName:"playbackButton"; text:"▶"; flat:true; Layout.preferredWidth:60; Layout.fillHeight:true; font.pixelSize:21; palette.buttonText:"#e6e9ef"; onClicked:cameraShell.navigate("photos"); Accessible.name:"Playback photographs" }
                Item { Layout.fillWidth:true }
                Button {
                    objectName:"shutterValueButton"
                    text:body.shutterLabel(camera.exposureUs)
                    flat:true
                    font.pixelSize:22
                    font.weight:Font.Medium
                    palette.buttonText:"#f3f5fa"
                    Layout.preferredWidth:130
                    Layout.fillHeight:true
                    onClicked:body.openQuick("exposure")
                    Accessible.name:"Requested shutter speed"
                }
                Button {
                    objectName:"gainValueButton"
                    text:"GAIN  " + camera.analogueGain.toFixed(2) + "×"
                    flat:true
                    font.pixelSize:15
                    palette.buttonText:"#e6e9ef"
                    Layout.preferredWidth:140
                    Layout.fillHeight:true
                    onClicked:body.openQuick("gain")
                    Accessible.name:"Requested analogue gain"
                }
                Item { Layout.fillWidth:true }
                Button { objectName:"quickButton"; text:"Q"; flat:true; Layout.preferredWidth:60; Layout.fillHeight:true; font.pixelSize:21; font.weight:Font.DemiBold; palette.buttonText:"#f3f5fa"; onClicked:body.openQuick("exposure"); Accessible.name:"Quick shooting controls" }
            }
        }
    }

    CameraMenu {
        id: menu
        objectName: "cameraMenu"
        anchors.fill:parent
        visible:body.currentScreen === "menu"
        shell:cameraShell
        cameraController:camera
        apps:cameraApps
        theme:body.menuTheme
        onCloseRequested:body.returnToCamera()
        onQuickRequested:body.openQuick(field)
        onToolRequested:cameraShell.navigate(app)
        onPlaybackRequested:cameraShell.navigate("photos")
    }
    CameraPlayback {
        id: playbackScene
        objectName:"cameraPlayback"
        anchors.fill:parent
        visible:body.currentScreen === "playback"
        cameraController:camera
        apps:cameraApps
        theme:body.menuTheme
        onCloseRequested:cameraShell.back()
    }
    AppsWorkspace {
        objectName:"cameraToolsWorkspace"
        anchors.fill:parent
        anchors.margins:12
        visible:body.currentScreen === "tool"
        shell:cameraShell
        cameraController:camera
        apps:cameraApps
        theme:body.menuTheme
    }
    Dialog {
        id: quickControls
        objectName:"quickControlsDialog"
        parent:Overlay.overlay
        modal:true
        focus:true
        x:(body.width-width)/2
        y:body.height-height-12
        width:Math.min(body.width-24,640)
        height:250
        padding:18
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background:Rectangle { color:"#f01a202a"; radius:8; border.color:"#515f72" }
        ColumnLayout {
            anchors.fill:parent
            spacing:12
            RowLayout {
                Layout.fillWidth:true
                Label { text:"QUICK CONTROL"; color:"#f3f5fa"; font.pixelSize:13; font.weight:Font.DemiBold; Layout.fillWidth:true }
                Button { objectName:"qCloseButton"; text:"Done"; implicitHeight:44; onClicked:quickControls.close() }
            }
            RowLayout {
                Layout.fillWidth:true
                spacing:16
                ColumnLayout {
                    Layout.fillWidth:true
                    Label { text:"Shutter speed"; color:"#b9c4d3"; font.pixelSize:12 }
                    ComboBox {
                        id:qExposure
                        objectName:"qExposureCombo"
                        Layout.fillWidth:true
                        implicitHeight:48
                        enabled:body.settingsReady
                        model:body.speeds
                        textRole:"label"
                        currentIndex:body.speedIndex(camera.exposureUs)
                        onActivated:camera.setExposureUs(model[index].us)
                        Accessible.name:"Requested shutter speed preset"
                    }
                }
                ColumnLayout {
                    Layout.fillWidth:true
                    Label { text:"Analogue gain"; color:"#b9c4d3"; font.pixelSize:12 }
                    SpinBox {
                        id:qGain
                        objectName:"qGainInput"
                        Layout.fillWidth:true
                        implicitHeight:48
                        enabled:body.settingsReady
                        from:100;to:1600;stepSize:25
                        value:Math.round(camera.analogueGain*100)
                        editable:true
                        validator:DoubleValidator {bottom:1;top:16;decimals:2;notation:DoubleValidator.StandardNotation;locale:qGain.locale.name}
                        textFromValue:function(value,locale) {return Number(value/100).toLocaleString(locale,"f",2)}
                        valueFromText:function(text,locale) {return Math.round(Number.fromLocaleString(locale,text)*100)}
                        onValueModified:camera.setAnalogueGain(value/100)
                        Accessible.name:"Requested analogue gain"
                    }
                }
            }
            RowLayout {
                Layout.fillWidth:true
                Label { text:"M  ·  RAW 14"; color:"#c3cedd"; font.pixelSize:12; Layout.fillWidth:true }
                CheckBox { objectName:"qGridSwitch"; text:"Grid"; checked:cameraShell.gridEnabled; onToggled:cameraShell.setPreference("grid_enabled",checked); palette.windowText:"#f3f5fa" }
                Button { text:"All settings"; implicitHeight:44; onClicked:{quickControls.close();cameraShell.navigate("settings")} }
            }
            Label { Layout.fillWidth:true; text:body.uncertain ? "Previous capture is unconfirmed." : body.settingsReady ? "Settings confirmed" : "Applying settings…"; color:body.uncertain ? "#f1d298" : "#b9c4d3"; font.pixelSize:11 }
        }
    }
    Dialog {
        id: acknowledgeDialog
        objectName:"acknowledgeCaptureDialog"
        parent:Overlay.overlay
        modal:true
        focus:true
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        x:(body.width-width)/2
        y:(body.height-height)/2
        width:Math.min(body.width-48,530)
        height:220
        padding:20
        background:Rectangle {color:body.menuTheme.surface;radius:8;border.color:body.menuTheme.line}
        ColumnLayout {
            anchors.fill:parent
            Label {text:"Allow a new photograph?";font.pixelSize:20;font.weight:Font.DemiBold;color:body.menuTheme.text}
            Label {Layout.fillWidth:true;text:"The previous capture will remain unconfirmed. A new shot may create another photograph.";wrapMode:Text.WordWrap;color:body.menuTheme.muted}
            RowLayout {
                Item {Layout.fillWidth:true}
                Button {text:"Back";implicitHeight:44;onClicked:acknowledgeDialog.close()}
                Button {objectName:"confirmNewShotButton";text:"Allow new shot";implicitHeight:44;enabled:camera.canAcknowledgeUnknown;onClicked:{camera.acknowledgeUnknown();acknowledgeDialog.close()}}
            }
        }
    }
}
