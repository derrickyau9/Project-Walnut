import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

MonochromeWindow {
    id: body
    objectName: "cameraFirstWindow"
    visible: true
    width: 800
    height: 480
    minimumWidth: 800
    minimumHeight: 480
    title: qsTr("DaOS Camera / FF-1")
    color: "#000000"
    Typography { id:typography }
    font.family: typography.uiFamily
    font.pixelSize: typography.bodySize
    font.weight: Font.Normal
    BootSplash {
        anchors.fill:parent
        z:20000
        visible:typeof bootStatus !== "undefined" && !bootStatus.ready
    }
    SoftKeyboard {
        parent: Overlay.overlay
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
    }
    Rectangle {
        parent:Overlay.overlay
        anchors.fill:parent
        z:50000
        visible:body.screenAsleep
        color:"#000000"
        MouseArea {anchors.fill:parent;acceptedButtons:Qt.AllButtons;onPressed:if(typeof systemControl !== "undefined") systemControl.wake()}
    }
    Rectangle {
        parent:Overlay.overlay;anchors.fill:parent;z:18000
        visible:!!camera.settingsView.maintenanceReason
        color:"#111111"
        focus:visible
        Keys.onPressed:event.accepted=true
        MouseArea {anchors.fill:parent;acceptedButtons:Qt.AllButtons}
        Column {
            anchors.centerIn:parent;spacing:24;width:Math.min(parent.width-64,560)
            BusyIndicator {anchors.horizontalCenter:parent.horizontalCenter;width:72;height:72;running:parent.parent.visible}
            Text {width:parent.width;text:camera.settingsView.maintenanceReason || qsTr("系统维护");color:"#ffffff";font.family:typography.titleFamily;font.pixelSize:24;font.weight:Font.Medium;font.letterSpacing:1.2;horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WordWrap}
            Text {width:parent.width;text:qsTr("正在安全处理配置，完成后恢复操作");color:"#bbbbbb";font.family:typography.uiFamily;font.pixelSize:18;horizontalAlignment:Text.AlignHCenter}
        }
    }
    readonly property bool screenAsleep:typeof systemControl !== "undefined" && systemControl.screenAsleep
    readonly property var menuTheme: cameraShell.theme === "dark" ? retailTheme.dark : retailTheme.light
    readonly property var workspace: typeof shootingWorkspace !== "undefined" ? shootingWorkspace : null
    readonly property bool textInputFocused: typeof softKeyboard !== "undefined" && softKeyboard.editing
    readonly property bool shootingShortcuts: currentScreen === "capture" && !screenAsleep && !camera.settingsView.maintenanceReason && !textInputFocused && !quickControls.visible && !workspaceDialog.visible && !acknowledgeDialog.visible
    readonly property bool reducedMotion: workspace && workspace.reducedMotion
    property real pageArrival: 1
    property var heldVisualState: ({})
    property int temporaryGrid: -1
    readonly property bool framingGridEnabled: temporaryGrid < 0 ? cameraShell.gridEnabled : temporaryGrid === 1
    readonly property string currentScreen: cameraShell.currentApp === "camera" ? "capture" : cameraShell.currentApp === "settings" ? "menu" : cameraShell.currentApp === "photos" ? "playback" : "tool"
    readonly property bool capturing: ["submitting", "accepted", "capturing"].indexOf(camera.captureState) >= 0
    readonly property bool uncertain: camera.captureState === "unknown" || camera.connectionState === "unknown"
    readonly property bool settingsReady: camera.settingsView.canEdit
    readonly property bool quickInputActive: workspaceDialog.visible && workspacePanel.page === "quick" && !textInputFocused && !screenAsleep && !camera.settingsView.maintenanceReason
    readonly property var speeds: camera.exposureSteps
    property string quickField: "exposure"
    property string toastRequest: ""
    property bool savedToast: false
    property bool liveViewDesired: true
    property bool taskbarExpanded: false
    property bool taskbarDragging: false
    readonly property real taskbarWidth: 72
    property real taskbarProgress: taskbarExpanded ? 1 : 0
    readonly property real taskbarInset: taskbarWidth * taskbarProgress
    readonly property real previewScale: (width-taskbarInset)/width
    readonly property bool taskbarAnimating: taskbarMotion.running
    onTaskbarAnimatingChanged:if(typeof uiPerformance !== "undefined") uiPerformance.setActivity("taskbar-motion",taskbarAnimating)
    readonly property bool taskbarGestureActive: taskbar.gestureActive || revealGesture.pressed
    Behavior on taskbarProgress {
        enabled: !body.taskbarDragging
        NumberAnimation { id:taskbarMotion; duration:body.reducedMotion ? 0 : 280; easing.type:Easing.OutCubic }
    }

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
        if(field === "workspace") { openWorkspace("quick"); return }
        quickField=field
        quickControls.open()
    }
    function openWorkspace(page) {
        quickControls.close()
        workspacePanel.page=page
        workspaceDialog.open()
    }
    function runAction(action) {
        if(action === "workspace" || action === "my_menu") { openWorkspace(action === "my_menu" ? "my_menu" : "customize"); return }
        workspaceDialog.close()
        if(action === "exposure" || action === "gain") openQuick(action)
        else if(action === "grid") cameraShell.setPreference("grid_enabled",!cameraShell.gridEnabled)
        else if(action === "theme") cameraShell.setPreference("theme",cameraShell.theme === "dark" ? "light" : "dark")
        else if(action === "clean_view") taskbarExpanded=false
        else if(action === "preview") { liveViewDesired=!camera.previewActive; camera.togglePreview() }
        else if(["wifi","terminal","tasks","image_tools","system_control"].indexOf(action)>=0) { cameraShell.selectGroup(action); cameraShell.navigate("settings") }
        else if(action === "system") { cameraShell.selectGroup("system"); cameraShell.navigate("settings") }
        else if(["photos","recipes","diagnostics"].indexOf(action) >= 0) cameraShell.navigate(action)
    }
    function stepDial(field,delta) {
        if(textInputFocused || uncertain || acknowledgeDialog.visible || !settingsReady || (currentScreen !== "capture" && !quickControls.visible)) return
        camera.nudgeSettings(field,delta,"dial")
    }
    function momentary(action,active) {
        if(active) {
            heldVisualState={grid:cameraShell.gridEnabled,taskbar:taskbarExpanded}
            if(action === "grid") temporaryGrid=cameraShell.gridEnabled ? 0 : 1
            else if(action === "clean_view") taskbarExpanded=false
            else runAction(action)
        } else {
            if(action === "grid") temporaryGrid=-1
            else if(action === "clean_view" && typeof heldVisualState.taskbar !== "undefined") taskbarExpanded=heldVisualState.taskbar
            else { quickControls.close(); workspaceDialog.close() }
            heldVisualState={}
        }
    }
    function settleTaskbar(expanded) {
        taskbarDragging=false
        taskbarExpanded=expanded
        taskbarProgress=Qt.binding(function() {return body.taskbarExpanded ? 1 : 0})
        if(expanded && currentScreen === "capture") taskbarHide.restart()
    }
    function shoot(hardware) {
        if(hardware ? currentScreen === "capture" && !uncertain : shootingShortcuts)
            camera.shutter()
    }
    function returnFromHardware() {
        if(screenAsleep && typeof systemControl !== "undefined")systemControl.wake()
        workspacePanel.closeOverlays()
        menu.closeOverlays()
        playbackScene.closeOverlay()
        quickControls.exit.enabled=false
        workspaceDialog.exit.enabled=false
        quickControls.opacity=0
        workspaceDialog.opacity=0
        quickControls.close()
        workspaceDialog.close()
        Qt.callLater(function() {quickControls.exit.enabled=true;workspaceDialog.exit.enabled=true})
        acknowledgeDialog.close()
        if(typeof softKeyboard !== "undefined") softKeyboard.done()
        cameraShell.navigate("camera")
    }
    function goBack() {
        if(textInputFocused) {softKeyboard.done();return}
        if(quickControls.visible) {quickControls.close();return}
        if(workspaceDialog.visible) {if(!workspacePanel.closeOverlays()) workspaceDialog.close();return}
        if(currentScreen === "menu" && menu.closeOverlays()) return
        if(currentScreen === "playback" && playbackScene.closeOverlay()) return
        if(currentScreen === "tool") cameraShell.back()
        else if(currentScreen !== "capture") returnToCamera()
    }
    function returnToCamera() {
        quickControls.close()
        workspaceDialog.close()
        cameraShell.navigate("camera")
    }
    onCurrentScreenChanged: {
        if(!reducedMotion) { pageArrival=.78; pageMotion.restart() }
        taskbarExpanded=currentScreen !== "capture" || (workspace && !workspace.taskbarAutoHide)
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
    palette.highlightedText: "#ffffff"
    palette.dark: menuTheme.muted

    Rectangle {
        objectName: "opaqueCameraBackground"
        anchors.fill: parent
        color: body.currentScreen === "capture" ? "#000000" : body.menuTheme.background
    }

    Shortcut { sequence: "Space"; autoRepeat: false; enabled: body.shootingShortcuts && !taskbar.keyboardFocusActive && !revealHandle.activeFocus; onActivated: body.shoot() }
    Shortcut { sequence: "Q"; autoRepeat: false; enabled: body.shootingShortcuts; onActivated: body.openQuick("workspace") }
    Shortcut { sequence: "M"; autoRepeat: false; enabled: body.shootingShortcuts; onActivated: cameraShell.navigate("settings") }
    Shortcut { sequence: "P"; autoRepeat: false; enabled: body.shootingShortcuts; onActivated: cameraShell.navigate("photos") }
    Shortcut { sequence: "Up"; autoRepeat: true; enabled: (body.shootingShortcuts || body.quickInputActive) && body.workspace; onActivated: body.workspace.dispatchInput("dial",1) }
    Shortcut { sequence: "Down"; autoRepeat: true; enabled: (body.shootingShortcuts || body.quickInputActive) && body.workspace; onActivated: body.workspace.dispatchInput("dial",-1) }
    Shortcut {
        sequence: "Escape"
        autoRepeat: false
        enabled: !quickControls.visible && !workspaceDialog.visible && !acknowledgeDialog.visible && !body.textInputFocused
        onActivated: {
            body.goBack()
        }
    }

    Timer {
        id: liveStart
        objectName: "autoPreviewTimer"
        interval: 250
        repeat: true
        running: body.currentScreen === "capture" && body.liveViewDesired && !body.screenAsleep
        onTriggered: {
            if(camera.connectionState === "ready" && camera.canCapture && !camera.previewActive && !body.uncertain)
                camera.togglePreview()
        }
    }
    Timer { id: savedTimer; interval: 2200; onTriggered: body.savedToast=false }
    NumberAnimation { id:pageMotion; target:body; property:"pageArrival"; to:1; duration:body.reducedMotion ? 0 : 280; easing.type:Easing.OutCubic;onRunningChanged:if(typeof uiPerformance !== "undefined") uiPerformance.setActivity("main-transition",running) }
    onShootingShortcutsChanged: if(workspace) workspace.setInputEnabled(shootingShortcuts)
    onQuickInputActiveChanged:if(workspace) workspace.setQuickActive(quickInputActive)
    Component.onCompleted: {
        if(workspace) {
            workspace.setInputEnabled(shootingShortcuts)
            workspace.setQuickActive(quickInputActive)
            workspace.setCurrentSettings({grid_enabled:cameraShell.gridEnabled,theme:cameraShell.theme})
            if(typeof systemControl !== "undefined") workspace.setDeveloperMode(systemControl.preferences.developer_mode)
            taskbarExpanded=currentScreen !== "capture" || !workspace.taskbarAutoHide
        }
    }
    Connections {
        target:cameraShell
        function onChanged() { if(body.workspace) body.workspace.setCurrentSettings({grid_enabled:cameraShell.gridEnabled,theme:cameraShell.theme}) }
    }
    Connections {target:body;function onFrameSwapped(){camera.noteSettingsVisible()}}
    Connections {
        target:typeof systemControl !== "undefined" ? systemControl : null
        function onChanged() {
            if(body.workspace) body.workspace.setDeveloperMode(systemControl.preferences.developer_mode)
            if(!systemControl.preferences.developer_mode && cameraShell.currentGroup === "terminal") menu.chooseCategory("system_control")
            if(body.screenAsleep && camera.previewActive) camera.togglePreview()
            else if(!body.screenAsleep && body.currentScreen === "capture" && body.liveViewDesired && camera.canCapture && !camera.previewActive) camera.togglePreview()
        }
    }
    Connections {
        target:typeof cameraInput !== "undefined" ? cameraInput : null
        function onReturnToCapture() {body.returnFromHardware()}
        function onShutterRequested() {body.shoot(true)}
        function onDialRequested(delta) {if(body.workspace) body.workspace.dispatchInput("dial",delta)}
        function onFnRequested(key,pressed) {
            if(!body.workspace) return
            if(!pressed) body.workspace.endInput(key)
            else if(body.shootingShortcuts) body.workspace.beginInput(key)
        }
        function onBackRequested() {body.goBack()}
    }
    Timer { id:taskbarHide; interval:2200; onTriggered:if(body.currentScreen === "capture" && !taskbar.gestureActive && !taskbar.keyboardNavigation && (!body.workspace || body.workspace.taskbarAutoHide)) body.taskbarExpanded=false }
    onTaskbarExpandedChanged: if(taskbarExpanded && currentScreen === "capture") taskbarHide.restart()
    Connections {
        target: body.workspace
        function onActionRequested(action) { body.runAction(action) }
        function onAdjustRequested(field,delta) { body.stepDial(field,delta) }
        function onMomentaryRequested(action,active) { body.momentary(action,active) }
        function onProfileApplied(prefs) {
            cameraShell.setPreference("grid_enabled",prefs.grid_enabled)
            cameraShell.setPreference("theme",prefs.theme)
        }
        function onChanged() { if(body.currentScreen === "capture" && body.workspace && !body.workspace.taskbarAutoHide) body.taskbarExpanded=true }
    }
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
        id: cameraContent
        objectName: "cameraContentPane"
        x: body.taskbarInset
        width: body.width-body.taskbarInset
        height: body.height
        clip: true
        opacity:body.pageArrival

        Item {
            id: shooting
            anchors.fill: parent
            visible: body.currentScreen === "capture"
            Rectangle { anchors.fill: parent; color: "#000000" }
            Item {
                id: pictureSlot
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: exposureBar.top
                Item {
                id: pictureArea
                objectName: "viewfinderArea"
                anchors.centerIn: parent
                width: parent.width
                height: Math.max(1,pictureSlot.height*body.previewScale)
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
                    Accessible.name: qsTr("HF-Sim live view, independently synthesized preview")
                }
                Item {
                    anchors.centerIn: parent
                    width: liveImage.paintedWidth > 0 ? liveImage.paintedWidth : parent.width
                    height: liveImage.paintedHeight > 0 ? liveImage.paintedHeight : parent.height
                    visible: body.framingGridEnabled && liveImage.visible
                    Repeater { model:2; Rectangle { x:(index+1)*parent.width/3; width:1; height:parent.height; color:"white"; opacity:.3 } }
                    Repeater { model:2; Rectangle { y:(index+1)*parent.height/3; width:parent.width; height:1; color:"white"; opacity:.3 } }
                }
                Text {
                    anchors.centerIn: parent
                    visible: !liveImage.visible && !body.uncertain
                    text: body.capturing ? qsTr("正在保存 RAW…") : camera.connectionState === "connecting" ? qsTr("正在连接相机…") : camera.connectionState === "ready" ? qsTr("实时取景已暂停") : qsTr("相机暂不可用")
                    color: "#dddddd"
                    font.family: body.font.family
                    font.pixelSize: 20
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.margins: 12
                    width: topLeft.implicitWidth+20
                    height: 32
                    color: "#000000"
                    radius: 3
                    Text { id:topLeft; anchors.centerIn:parent; text:qsTr("M   RAW 14"); color:"white"; font.family:typography.numericFamily; font.pixelSize:18; font.weight:Font.Medium; font.letterSpacing:.8 }
                }
                Rectangle {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 12
                    width: 72
                    height: 28
                    color: "#000000"
                    radius: 3
                    Text { anchors.centerIn:parent; text:qsTr("HF-Sim"); color:"#dddddd"; font.family:typography.uiFamily; font.pixelSize:typography.captionSize }
                }
                Rectangle {
                    anchors.horizontalCenter:parent.horizontalCenter;anchors.top:parent.top;anchors.topMargin:12
                    width:260;height:32;color:"#111111";radius:3
                    visible:camera.settingsPending || camera.settingsView.isTemporary
                    Text {
                        anchors.centerIn:parent;color:"#eeeeee";font.family:typography.uiFamily;font.pixelSize:14
                        text:camera.settingsView.temporaryState === "restoring" ? qsTr("正在恢复原参数") : camera.settingsView.isTemporary ? qsTr("Fn 临时参数 · 松开恢复") : body.capturing ? qsTr("下一张参数 · 等待本张完成") : qsTr("参数确认中 · 请稍后按快门")
                    }
                }
                Button {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 18
                    objectName:"savedPhotoReviewButton"
                    width:264
                    height:72
                    visible: body.savedToast || body.capturing
                    enabled:!body.capturing
                    onClicked:{cameraShell.navigate("photos");camera.refreshLibrary()}
                    background:Rectangle {color:"#111111";radius:4;border.color:"#444444"}
                    contentItem:RowLayout {
                        spacing:12
                        Image { Layout.preferredWidth:76;Layout.fillHeight:true;source:camera.lastCaptureThumbnail;fillMode:Image.PreserveAspectFit;visible:source.toString().length>0 }
                        ColumnLayout {
                            Layout.fillWidth:true;spacing:4
                            Text { text:body.capturing ? qsTr("正在保存…") : qsTr("RAW 已保存");color:"#ffffff";font.family:typography.uiFamily;font.pixelSize:18 }
                            Text { text:body.capturing ? qsTr("按原拍摄请求写入") : qsTr("点击查看照片");color:"#bbbbbb";font.family:typography.uiFamily;font.pixelSize:14 }
                        }
                    }
                }
                Button {
                    id: touchShutter
                    objectName: "shootButton"
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 20
                    width: 64; height: 64
                    autoRepeat: false
                    enabled: camera.canCapture && !quickControls.visible && !workspaceDialog.visible && !body.uncertain && !body.textInputFocused
                    onClicked: body.shoot()
                    Accessible.name: qsTr("Take one photograph")
                    background: Rectangle {
                        radius: 32
                        color: "#111111"
                        border.width: 2
                        border.color: touchShutter.enabled ? "#ffffff" : "#777777"
                        Rectangle { anchors.centerIn:parent; width:46; height:46; radius:23; color:touchShutter.down ? "#666666" : touchShutter.enabled ? "#ffffff" : "#666666"; scale:touchShutter.down ? .88 : 1; Behavior on scale { NumberAnimation { duration:body.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } } }
                    }
                }
                Rectangle {
                    objectName: "captureRecoveryPanel"
                    visible: body.uncertain
                    anchors.centerIn: parent
                    width: Math.min(parent.width-48,620)
                    height: recoveryBody.implicitHeight+36
                    radius: 8
                    color: "#111111"
                    border.color: "#aaaaaa"
                    ColumnLayout {
                        id: recoveryBody
                        anchors.fill:parent
                        anchors.margins:18
                        spacing:12
                        Label { text:qsTr("上次拍摄结果尚未确认"); color:"#dddddd"; font.family:typography.titleFamily; font.pixelSize:typography.sectionSize; font.weight:Font.Medium; font.letterSpacing:typography.titleTracking }
                        Label { Layout.fillWidth:true; text:qsTr("请先查询上次拍摄，防止重复保存照片。"); color:"#eeeeee"; wrapMode:Text.WordWrap; font.pixelSize:18 }
                        RowLayout {
                            Layout.fillWidth:true
                            Button { objectName:"queryOriginalButton"; text:qsTr("查询拍摄"); enabled:camera.lastRequestId.length>0 && !camera.busy; onClicked:camera.queryLast(); implicitHeight:64 }
                            Button { objectName:"reconnectButton"; text:qsTr("重新连接"); enabled:!camera.busy; onClicked:camera.reconnect(); implicitHeight:64 }
                            Button { objectName:"acknowledgeUnknownButton"; text:qsTr("另拍一张…"); enabled:camera.canAcknowledgeUnknown; onClicked:acknowledgeDialog.open(); implicitHeight:64 }
                        }
                    }
                }
                Rectangle {
                    objectName:"temporarySettingsRecoveryPanel"
                    visible:camera.settingsView.recoveryRequired && !body.uncertain
                    anchors.centerIn:parent
                    width:Math.min(parent.width-32,660)
                    height:recoverySettingsBody.implicitHeight+32
                    color:"#111111";radius:6;border.color:"#777777"
                    ColumnLayout {
                        id:recoverySettingsBody
                        anchors.fill:parent;anchors.margins:16;spacing:12
                        Label {text:qsTr("临时参数尚未恢复");color:"#ffffff";font.family:typography.titleFamily;font.pixelSize:24;font.weight:Font.Medium;font.letterSpacing:1.2}
                        Label {Layout.fillWidth:true;text:camera.settingsView.error || qsTr("请先核对相机，再恢复原参数或明确保留当前值。");color:"#cccccc";font.pixelSize:18;wrapMode:Text.WordWrap}
                        RowLayout {
                            Layout.fillWidth:true
                            Button {text:qsTr("查询原拍摄");implicitHeight:64;visible:camera.lastRequestId.length>0;enabled:!camera.busy;onClicked:camera.queryLast()}
                            Button {text:qsTr("恢复原参数");implicitHeight:64;Layout.fillWidth:true;enabled:camera.settingsView.canRestoreTemporary;onClicked:camera.reconcileTemporary()}
                            Button {text:qsTr("保留当前参数");implicitHeight:64;Layout.fillWidth:true;enabled:camera.settingsView.canDiscardTemporary;onClicked:camera.discardTemporaryRecovery()}
                        }
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
                color: "#111111"
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin:10
                    anchors.rightMargin:10
                    spacing: 4
                    Button { objectName:"menuButton"; text:qsTr("菜单"); flat:true; Layout.preferredWidth:78; Layout.fillHeight:true; font.pixelSize:18; palette.buttonText:"#eeeeee"; onClicked:cameraShell.navigate("settings"); Accessible.name:qsTr("相机菜单") }
                    Button { objectName:"playbackButton"; text:"▶"; flat:true; Layout.preferredWidth:64; Layout.fillHeight:true; font.pixelSize:21; palette.buttonText:"#eeeeee"; onClicked:cameraShell.navigate("photos"); Accessible.name:qsTr("Playback photographs") }
                    Item { Layout.fillWidth:true }
                    Button {
                        objectName:"shutterValueButton"
                        text:body.shutterLabel(camera.exposureUs)
                        flat:true
                        font.family:typography.numericFamily
                        font.pixelSize:typography.numericSize
                        font.weight:Font.Normal
                        font.letterSpacing:typography.numericTracking
                        palette.buttonText:"#ffffff"
                        Layout.preferredWidth:156
                        Layout.fillHeight:true
                        onClicked:body.openQuick("exposure")
                        Accessible.name:qsTr("Requested shutter speed")
                        contentItem:NumericReadout {value:body.shutterLabel(camera.exposureUs);pixelSize:28;reduceMotion:body.reducedMotion;activityName:"main-shutter-value"}
                    }
                    Button {
                        objectName:"gainValueButton"
                        text:qsTr("增益 ") + camera.analogueGain.toFixed(2) + "×"
                        flat:true
                        font.family:typography.numericFamily
                        font.pixelSize:20
                        font.weight:Font.Normal
                        font.letterSpacing:typography.numericTracking
                        palette.buttonText:"#eeeeee"
                        Layout.preferredWidth:156
                        Layout.fillHeight:true
                        onClicked:body.openQuick("gain")
                        Accessible.name:qsTr("Requested analogue gain")
                        contentItem:NumericReadout {value:qsTr("增益 ")+camera.analogueGain.toFixed(2)+"×";pixelSize:20;reduceMotion:body.reducedMotion;activityName:"main-gain-value"}
                    }
                    Item { Layout.fillWidth:true }
                    Button { objectName:"quickButton"; text:"Q"; flat:true; Layout.preferredWidth:64; Layout.fillHeight:true; font.pixelSize:21; font.weight:Font.DemiBold; palette.buttonText:"#ffffff"; onClicked:body.openQuick("workspace"); Accessible.name:qsTr("Twelve customizable quick shooting controls") }
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
            workspace:body.workspace
            onWorkspaceRequested:body.openWorkspace(page)
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
    }

    CameraTaskbar {
        id: taskbar
        objectName: "cameraTaskbar"
        x: body.taskbarInset-body.taskbarWidth
        width: body.taskbarWidth
        height: body.height
        z: 50
        clip: true
        visible: body.taskbarProgress > 0
        shell: cameraShell
        theme: body.menuTheme
        expanded: body.taskbarExpanded
        onCollapseRequested: body.taskbarExpanded=false
        onExpandRequested: body.taskbarExpanded=true
        onDragProgressRequested: { body.taskbarDragging=true; body.taskbarProgress=progress }
        onDragFinished: body.settleTaskbar(expanded)
        onNavigateRequested: { quickControls.close(); workspaceDialog.close(); cameraShell.navigate(appId); if(appId === "camera") body.taskbarExpanded=false }
    }
    Button {
        id: revealHandle
        objectName: "taskbarRevealHandle"
        x: 0
        y: (body.height-height)/2
        width: 64
        height: 64
        padding: 0
        z: 51
        visible: !body.taskbarExpanded
        onClicked: body.settleTaskbar(true)
        Accessible.name: qsTr("Show Taskbar")
        contentItem: Item { DaOSIcon { anchors.centerIn:parent; kind:"back"; rotation:180; ink:body.menuTheme.text; width:16; height:16 } }
        background: Rectangle { color:revealHandle.down ? "#666666" : body.menuTheme.surface; border.color:body.menuTheme.line; radius:4 }
    }
    MouseArea {
        id: revealGesture
        objectName: "taskbarRevealGesture"
        x: 0; y: 0; width: 64; height: body.height
        z: 52
        visible: !body.taskbarExpanded || body.taskbarDragging
        acceptedButtons: Qt.LeftButton
        preventStealing: true
        property real pressX: 0
        property real pressY: 0
        property bool moved: false
        onPressed: {
            var overHandle=mouse.y>=revealHandle.y && mouse.y<=revealHandle.y+revealHandle.height
            // A 64px handle and a narrow screen-edge swipe must not steal the
            // real menu/playback controls underneath the rest of this strip.
            if(!overHandle && (mouse.x>14 || mouse.y>=body.height-72)) {mouse.accepted=false;return}
            pressX=mouse.x; pressY=mouse.y; moved=false
        }
        onPositionChanged: {
            if(!pressed) return
            var dx=mouse.x-pressX,dy=mouse.y-pressY
            if(Math.abs(dx)>8 || Math.abs(dy)>8) moved=true
            if(dx>8 && dx>Math.abs(dy)*1.5) { body.taskbarDragging=true; body.taskbarProgress=Math.max(0,Math.min(1,dx/body.taskbarWidth)) }
        }
        onReleased: {
            var dx=mouse.x-pressX,dy=mouse.y-pressY
            if(dx>=32 && dx>=Math.abs(dy)*1.5)
                body.settleTaskbar(true)
            else if(!moved && Math.abs(dx)<8 && Math.abs(dy)<8 && mouse.y>=revealHandle.y && mouse.y<=revealHandle.y+revealHandle.height)
                body.settleTaskbar(true)
            else body.settleTaskbar(false)
        }
        onCanceled: body.settleTaskbar(body.taskbarProgress > .5)
    }
    Dialog {
        id:workspaceDialog
        objectName:"shootingWorkspaceDialog"
        parent:Overlay.overlay
        modal:true
        focus:true
        x:16; y:12
        width:body.width-32
        height:body.height-24
        padding:12
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background:Rectangle { color:body.menuTheme.surface; radius:8; border.color:body.menuTheme.line }
        onClosed:if(body.currentScreen === "capture" && (!body.workspace || body.workspace.taskbarAutoHide)) body.taskbarExpanded=false
        enter:Transition { ParallelAnimation { NumberAnimation { property:"opacity"; from:0; to:1; duration:body.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } NumberAnimation { property:"scale"; from:body.reducedMotion ? 1 : .97; to:1; duration:body.reducedMotion ? 0 : 280; easing.type:Easing.OutCubic } } }
        exit:Transition { NumberAnimation { property:"opacity"; to:0; duration:body.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } }
        ColumnLayout {
            anchors.fill:parent
            spacing:6
            RowLayout {
                Layout.fillWidth:true
                Label { text:workspacePanel.page === "quick" ? qsTr("快捷控制") : workspacePanel.page === "my_menu" ? qsTr("我的菜单") : qsTr("拍摄工作台"); color:body.menuTheme.text; font.family:typography.titleFamily; font.pixelSize:typography.titleSize; font.weight:Font.Medium; font.letterSpacing:typography.titleTracking; Layout.fillWidth:true }
                Button {
                    id:quickPhotoMode;visible:workspacePanel.page === "quick";text:qsTr("照片");implicitWidth:88;implicitHeight:64
                    readonly property bool selected:body.workspace && body.workspace.mode === "photo"
                    onClicked:body.workspace.setMode("photo")
                    background:Rectangle {color:quickPhotoMode.selected ? body.menuTheme.text : body.menuTheme.raised;border.color:body.menuTheme.line}
                    contentItem:Text {text:quickPhotoMode.text;color:quickPhotoMode.selected ? body.menuTheme.background : body.menuTheme.text;font.pixelSize:18;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter}
                }
                Button {
                    id:quickVideoMode;visible:workspacePanel.page === "quick";text:qsTr("视频");implicitWidth:88;implicitHeight:64
                    readonly property bool selected:body.workspace && body.workspace.mode === "video"
                    onClicked:body.workspace.setMode("video")
                    background:Rectangle {color:quickVideoMode.selected ? body.menuTheme.text : body.menuTheme.raised;border.color:body.menuTheme.line}
                    contentItem:Text {text:quickVideoMode.text;color:quickVideoMode.selected ? body.menuTheme.background : body.menuTheme.text;font.pixelSize:18;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter}
                }
                Button {visible:workspacePanel.page === "quick";text:workspacePanel.editing ? qsTr("完成编辑") : qsTr("编辑 12 格");implicitHeight:64;onClicked:workspacePanel.editing=!workspacePanel.editing}
                Button { text:qsTr("完成"); implicitHeight:64;implicitWidth:80; onClicked:workspaceDialog.close() }
            }
            Rectangle { Layout.fillWidth:true; Layout.preferredHeight:1; color:body.menuTheme.line; opacity:.65 }
            ShootingWorkspacePanel { id:workspacePanel; Layout.fillWidth:true; Layout.fillHeight:true; workspace:body.workspace; theme:body.menuTheme;showModeToolbar:page !== "quick" }
        }
    }
    Dialog {
        id: quickControls
        objectName:"quickControlsDialog"
        parent:Overlay.overlay
        modal:true
        focus:true
        x:body.taskbarInset+(cameraContent.width-width)/2
        y:body.textInputFocused ? 8 : body.height-height-12
        width:Math.min(cameraContent.width-24,640)
        height:body.textInputFocused ? Math.max(180,body.height-264) : 336
        padding:body.textInputFocused ? 10 : 18
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background:Rectangle { color:"#111111"; radius:8; border.color:"#555555" }
        enter:Transition { NumberAnimation { property:"opacity"; from:0; to:1; duration:body.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } }
        exit:Transition { NumberAnimation { property:"opacity"; to:0; duration:body.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } }
        ColumnLayout {
            anchors.fill:parent
            spacing:body.textInputFocused ? 4 : 10
            RowLayout {
                Layout.fillWidth:true
                Label { text:qsTr("曝光控制"); color:"#ffffff"; font.family:typography.titleFamily; font.pixelSize:typography.titleSize; font.weight:Font.Medium; font.letterSpacing:typography.titleTracking; Layout.fillWidth:true }
                Button { objectName:"qCloseButton"; text:qsTr("完成"); implicitHeight:64; onClicked:quickControls.close() }
            }
            RowLayout {
                Layout.fillWidth:true
                spacing:16
                ColumnLayout {
                    Layout.fillWidth:true
                    Label { text:qsTr("快门速度 · 1/3 EV"); color:"#cccccc"; font.pixelSize:18 }
                    LocalizedComboBox {
                        id:qExposure
                        objectName:"qExposureCombo"
                        Layout.fillWidth:true
                        implicitHeight:64
                        enabled:body.settingsReady
                        model:body.speeds
                        textRole:"label"
                        currentIndex:body.speedIndex(camera.exposureUs)
                        onActivated:camera.setExposureUs(model[index].us)
                        delegate:ItemDelegate {width:qExposure.width;height:64;text:qsTr(modelData.label);highlighted:qExposure.highlightedIndex === index;font.pixelSize:18}
                        Accessible.name:qsTr("Requested shutter speed preset")
                    }
                }
                ColumnLayout {
                    Layout.fillWidth:true
                    Label { text:qsTr("模拟增益"); color:"#cccccc"; font.pixelSize:18 }
                    SpinBox {
                        id:qGain
                        objectName:"qGainInput"
                        Layout.fillWidth:true
                        implicitHeight:64
                        enabled:body.settingsReady
                        from:100;to:1600;stepSize:25
                        value:Math.round(camera.analogueGain*100)
                        editable:true
                        validator:DoubleValidator {bottom:1;top:16;decimals:2;notation:DoubleValidator.StandardNotation;locale:qGain.locale.name}
                        textFromValue:function(value,locale) {return Number(value/100).toLocaleString(locale,"f",2)}
                        valueFromText:function(text,locale) {return Math.round(Number.fromLocaleString(locale,text)*100)}
                        onValueModified:camera.setAnalogueGain(value/100)
                        Accessible.name:qsTr("Requested analogue gain")
                        palette.text:"#ffffff"
                        palette.base:"#222222"
                        up.indicator:Rectangle {
                            objectName:"qGainIncrease"
                            x:qGain.width-width
                            width:64;height:qGain.height
                            color:qGain.up.pressed ? "#555555" : qGain.up.hovered ? "#444444" : "#333333"
                            Text { anchors.centerIn:parent;text:"+";color:qGain.enabled ? "#ffffff" : "#999999";font.family:body.font.family;font.pixelSize:24 }
                        }
                        down.indicator:Rectangle {
                            objectName:"qGainDecrease"
                            x:0
                            width:64;height:qGain.height
                            color:qGain.down.pressed ? "#555555" : qGain.down.hovered ? "#444444" : "#333333"
                            Text { anchors.centerIn:parent;text:"\u2212";color:qGain.enabled ? "#ffffff" : "#999999";font.family:body.font.family;font.pixelSize:24 }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth:true
                visible:!body.textInputFocused
                Label { text:qsTr("M  ·  RAW 14"); color:"#cccccc"; font.family:typography.numericFamily; font.pixelSize:typography.captionSize; font.letterSpacing:.6; Layout.fillWidth:true }
                CheckBox { objectName:"qGridSwitch"; text:qsTr("构图网格"); checked:cameraShell.gridEnabled; onToggled:cameraShell.setPreference("grid_enabled",checked); palette.windowText:"#ffffff" }
                Button { text:qsTr("全部设置"); implicitHeight:64; onClicked:{quickControls.close();cameraShell.navigate("settings")} }
            }
            Label { Layout.fillWidth:true; visible:!body.textInputFocused; text:body.uncertain ? qsTr("上次拍摄结果尚未确认") : body.settingsReady ? qsTr("参数已由相机确认 · 增益尚未标定为 ISO") : qsTr("正在应用参数…"); color:body.uncertain ? "#dddddd" : "#cccccc"; font.pixelSize:14 }
        }
    }
    Dialog {
        id: acknowledgeDialog
        objectName:"acknowledgeCaptureDialog"
        parent:Overlay.overlay
        modal:true
        focus:true
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        x:body.taskbarInset+(cameraContent.width-width)/2
        y:(body.height-height)/2
        width:Math.min(cameraContent.width-48,530)
        height:220
        padding:20
        background:Rectangle {color:body.menuTheme.surface;radius:8;border.color:body.menuTheme.line}
        ColumnLayout {
            anchors.fill:parent
            Label {text:qsTr("允许另拍一张？");font.family:typography.titleFamily;font.pixelSize:typography.titleSize;font.weight:Font.Medium;font.letterSpacing:typography.titleTracking;color:body.menuTheme.text}
            Label {Layout.fillWidth:true;text:qsTr("上次拍摄仍会保留为未确认。再次按下快门可能额外生成一张照片。");wrapMode:Text.WordWrap;font.pixelSize:18;color:body.menuTheme.muted}
            RowLayout {
                Item {Layout.fillWidth:true}
                Button {text:qsTr("返回");implicitHeight:64;onClicked:acknowledgeDialog.close()}
                Button {objectName:"confirmNewShotButton";text:qsTr("允许新拍摄");implicitHeight:64;enabled:camera.canAcknowledgeUnknown;onClicked:{camera.acknowledgeUnknown();acknowledgeDialog.close()}}
            }
        }
    }
}
