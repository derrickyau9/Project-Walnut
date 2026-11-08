import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: menu
    objectName: "cameraMenu"
    property var shell
    property var cameraController
    property var apps
    property var theme
    property var workspace
    property bool toolsSelected: false
    Typography { id:typography }
    signal closeRequested()
    signal quickRequested(string field)
    signal toolRequested(string app)
    signal playbackRequested()
    signal workspaceRequested(string page)

    readonly property var fallbackTheme: ({background:"#000000",surface:"#111111",raised:"#222222",layer:"#333333",text:"#ffffff",muted:"#cccccc",helper:"#aaaaaa",line:"#444444",accent:"#666666",accentHover:"#777777",softAccent:"#222222",focus:"#ffffff"})
    readonly property var colors: theme && theme.background ? theme : fallbackTheme
    readonly property string uiFont: typography.uiFamily
    readonly property string groupId: toolsSelected ? "tools" : shell ? shell.currentGroup : "exposure"
    readonly property bool settingsEnabled: cameraController && cameraController.settingsView.canEdit
    readonly property string menuError: shell && shell.errorText.length > 0 ? shell.errorText : cameraController ? cameraController.errorText : ""
    color: colors.background
    focus: visible

    function categoryTitle(id) {
        if (id === "tools") return qsTr("工具")
        var groups = shell ? shell.settingsGroups : []
        for (var i = 0; i < groups.length; i++)
            if (groups[i].id === id) return qsTr(groups[i].label || groups[i].title)
        return qsTr("曝光")
    }
    function chooseCategory(id) {
        if(id === "terminal" && typeof systemControl !== "undefined" && !systemControl.preferences.developer_mode) id="system_control"
        if (id === "tools") {
            toolsSelected = true
        } else {
            toolsSelected = false
            if (shell) shell.selectGroup(id)
        }
    }
    function closeOverlays() {return !!(page.item && typeof page.item.closeOverlays === "function" && page.item.closeOverlays())}
    function exposureLabel(value) {
        if (!(value > 0)) return "--"
        if (value >= 1000000) return (value / 1000000).toFixed(value % 1000000 ? 1 : 0) + " s"
        var denominator = 1000000 / value
        if (Math.abs(denominator - Math.round(denominator)) < 0.001)
            return "1/" + Math.round(denominator) + " s"
        return value >= 1000 ? (value / 1000).toFixed(1) + " ms" : value + " us"
    }
    function rowLabel(value) {
        var labels={"System":"系统", "Runtime architecture":"运行架构", "Python / Qt":"Python / Qt", "Data filesystem free":"数据空间可用", "Data filesystem total":"数据空间总量", "Data location":"数据位置", "Updates":"系统更新", "Local app data":"应用数据", "Portable recipes":"拍摄配方数量", "Export folder":"导出文件夹", "Storage validation":"存储验证"}
        return labels[value] ? qsTr(labels[value]) : value
    }
    function rowValue(value) {
        if(value === "BSD A/B update integration is unavailable") return qsTr("尚未接入 BSD A/B 系统更新")
        if(value === "Capacity only; media speed and power-loss behavior are untested") return qsTr("仅确认容量，尚未验证介质速度与断电保存能力")
        return value
    }
    onVisibleChanged: if (visible && apps) apps.refreshDiagnostics()
    Component.onCompleted: if (visible && apps) apps.refreshDiagnostics()
    Keys.onEscapePressed: { closeRequested(); event.accepted = true }

    Rectangle {
        id: header
        visible:menu.groupId !== "terminal"
        height: 64
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        color: menu.colors.surface
        Text { anchors.left: parent.left; anchors.leftMargin: 18; anchors.verticalCenter: parent.verticalCenter; text: qsTr("相机菜单"); color: menu.colors.text; font.family: typography.titleFamily; font.pixelSize: typography.titleSize; font.weight: Font.Medium; font.letterSpacing: typography.titleTracking }
        Button {
            id: closeButton
            objectName: "menuBackButton"
            anchors.right: parent.right
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            width: 156
            height: 64
            onClicked: menu.closeRequested()
            background: Rectangle { color: closeButton.down || closeButton.activeFocus ? menu.colors.accent : menu.colors.surface }
            contentItem: Row {
                spacing: 10
                DaOSIcon { width: 19; height: 19; anchors.verticalCenter: parent.verticalCenter; kind: "back"; ink: closeButton.down || closeButton.activeFocus ? "#ffffff" : menu.colors.text }
                Text { text: qsTr("返回拍摄"); anchors.verticalCenter: parent.verticalCenter; color: closeButton.down || closeButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
            }
        }
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: menu.colors.line }
    }

    Rectangle {
        id: categories
        visible:menu.groupId !== "terminal"
        anchors.left: parent.left
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        width: 144
        color: menu.colors.surface
        Flickable {
            id:categoryViewport
            anchors.fill:parent
            clip:true
            contentHeight:categoryColumn.height
            boundsBehavior:Flickable.StopAtBounds
            ScrollIndicator.vertical:ScrollIndicator { }
            function reveal(entry) {
                if(entry.y<contentY) contentY=entry.y
                else if(entry.y+entry.height>contentY+height) contentY=entry.y+entry.height-height
            }
        Column {
            id:categoryColumn
            width: parent.width
            Repeater {
                model: menu.shell ? menu.shell.settingsGroups : []
                Button {
                    id: categoryButton
                    objectName: "menuCategory-" + modelData.id
                    width: categories.width
                    height: 64
                    property bool selected: menu.groupId === modelData.id
                    onSelectedChanged:if(selected) revealCategory.restart()
                    Timer {id:revealCategory;interval:0;onTriggered:categoryViewport.reveal(categoryButton)}
                    onClicked: menu.chooseCategory(modelData.id)
                    background: Rectangle { color: categoryButton.selected ? menu.colors.accent : categoryButton.down || categoryButton.activeFocus ? menu.colors.raised : menu.colors.surface }
                    contentItem: Text { text: qsTr(modelData.label || modelData.title); textFormat: Text.PlainText; leftPadding: 12; verticalAlignment: Text.AlignVCenter; wrapMode:Text.WordWrap;maximumLineCount:2;elide:Text.ElideRight; color: categoryButton.selected ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: typography.bodySize; font.letterSpacing:typography.bodyTracking; font.weight: categoryButton.selected ? Font.Medium : Font.Normal }
                }
            }
            Button {
                id: toolsButton
                objectName: "menuCategory-tools"
                width: categories.width
                height: 64
                onClicked: menu.chooseCategory("tools")
                background: Rectangle { color: menu.toolsSelected ? menu.colors.accent : toolsButton.down || toolsButton.activeFocus ? menu.colors.raised : menu.colors.surface }
                contentItem: Text { text: qsTr("工具"); leftPadding: 9; verticalAlignment: Text.AlignVCenter; color: menu.toolsSelected ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18; font.weight: menu.toolsSelected ? Font.DemiBold : Font.Normal }
            }
        }
        }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 1; color: menu.colors.line }
    }

    ColumnLayout {
        id: panel
        visible:menu.groupId !== "terminal"
        anchors.left: categories.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        spacing: 0
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: menu.categoryTitle(menu.groupId); color: menu.colors.text; font.family: typography.titleFamily; font.pixelSize: typography.sectionSize; font.weight:Font.Medium; font.letterSpacing:typography.titleTracking }
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: menu.colors.line }
        }
        AnimatedPageLoader {
            id: page
            reduceMotion:menu.workspace && menu.workspace.reducedMotion
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: menu.groupId === "my_menu" ? myMenuPage : menu.groupId === "workspace" ? workspacePage : menu.groupId === "capabilities" ? capabilitiesPage : menu.groupId === "system_control" ? systemControlsPage : menu.groupId === "tasks" ? tasksPage : menu.groupId === "image_tools" ? imageToolsPage : menu.groupId === "exposure" ? exposurePage : menu.groupId === "image" ? imagePage : menu.groupId === "display" ? displayPage : menu.groupId === "storage" ? storagePage : menu.groupId === "wifi" ? wifiPage : menu.groupId === "terminal" ? null : menu.groupId === "system" ? systemPage : toolsPage
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 50
            visible: menu.menuError.length > 0
            Rectangle { anchors.fill: parent; color: menu.colors.softAccent }
            Text { anchors.fill: parent; anchors.margins: 10; text: menu.menuError; textFormat: Text.PlainText; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight }
        }
    }

    Loader {
        anchors.fill:parent
        active:menu.groupId === "terminal"
        visible:active
        sourceComponent:terminalPage
    }
    Component { id:wifiPage; WifiPage { colors:menu.colors } }
    Component { id:myMenuPage; ShootingWorkspacePanel { anchors.fill:parent; anchors.margins:12; workspace:menu.workspace; theme:menu.colors; page:"my_menu" } }
    Component { id:workspacePage; ShootingWorkspacePanel { anchors.fill:parent; anchors.margins:12; workspace:menu.workspace; theme:menu.colors; page:"customize" } }
    Component { id:capabilitiesPage; CapabilityPage { cameraController:menu.cameraController; onCloseRequested:menu.chooseCategory("my_menu") } }
    Component {
        id:systemControlsPage
        Loader {
            source:"SystemPage.qml"
            function closeOverlays() {return !!(item && typeof item.closeOverlays === "function" && item.closeOverlays())}
            onLoaded: {
                item.systemController=typeof systemControl !== "undefined" ? systemControl : null
                item.closeRequested.connect(function(){menu.chooseCategory("my_menu")})
            }
        }
    }
    Component {
        id:tasksPage
        Loader {
            source:"TasksPage.qml"
            onLoaded:{item.taskService=menu.apps ? menu.apps.tasks : null;item.closeRequested.connect(function(){menu.chooseCategory("my_menu")})}
        }
    }
    Component {
        id:imageToolsPage
        Loader {
            source:"ImageTools.qml"
            onLoaded: {
                item.processing=typeof imageProcessing !== "undefined" ? imageProcessing : null
                item.selectedSources=Qt.binding(function(){return menu.apps && menu.apps.media && menu.apps.media.gallery && typeof menu.apps.media.gallery.selectedSources !== "undefined" ? menu.apps.media.gallery.selectedSources : []})
                item.analysis=Qt.binding(function(){return menu.apps && menu.apps.media && menu.apps.media.gallery ? menu.apps.media.gallery.analysis : ({})})
                item.selectionLimitReason=Qt.binding(function(){return menu.apps && menu.apps.media && menu.apps.media.gallery && menu.apps.media.gallery.selectionCount > 16 ? "图像计算最多使用 16 张输入；当前选择 " + menu.apps.media.gallery.selectionCount + " 张，请缩小选择。批量导出可处理整个选择。" : ""})
                if(menu.apps && typeof menu.apps.exportDirectory !== "undefined")item.exportDirectory=menu.apps.exportDirectory
                item.closeRequested.connect(function(){menu.chooseCategory("my_menu")})
            }
        }
    }
    Component { id:terminalPage; TerminalPage { onCloseRequested:menu.chooseCategory("system") } }
    Component {
        id: exposurePage
        Column {
            Item {
                width: parent.width; height: 64
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: qsTr("曝光模式"); color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                Text { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: qsTr("M  手动"); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Repeater {
                model: [{field:"exposure",label:"快门速度",value:menu.cameraController ? menu.exposureLabel(menu.cameraController.exposureUs) : "--"},{field:"gain",label:"模拟增益",value:menu.cameraController ? Number(menu.cameraController.analogueGain).toFixed(2) + "x" : "--"}]
                Button {
                    id: exposureRow
                    objectName: modelData.field === "exposure" ? "menuShutterSetting" : "menuGainSetting"
                    width: parent.width
                    height: 64
                    enabled: menu.settingsEnabled
                    onClicked: menu.quickRequested(modelData.field)
                    background: Rectangle { color: exposureRow.down || exposureRow.activeFocus ? menu.colors.accent : menu.colors.background; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                    contentItem: RowLayout {
                        spacing: 14
                        Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: qsTr(modelData.label); color: exposureRow.down || exposureRow.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18; opacity: exposureRow.enabled ? 1 : 0.5 }
                        Text { text: modelData.value; color: exposureRow.down || exposureRow.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18; opacity: exposureRow.enabled ? 1 : 0.5 }
                        Text { Layout.rightMargin: 11; text: ">"; color: exposureRow.down || exposureRow.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                    }
                }
            }
            Text { width: parent.width - 32; x: 16; topPadding: 16; text: menu.settingsEnabled ? qsTr("设置下一张照片的快门和增益，参数由相机确认后生效。") : qsTr("请等待相机就绪，并确认上次拍摄结果后再调整曝光。"); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14; wrapMode: Text.WordWrap }
        }
    }

    Component {
        id: imagePage
        Column {
            Item {
                width: parent.width; height: 64
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: qsTr("合成画面模式"); color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                LocalizedComboBox {
                    id: sensorMode
                    objectName: "menuSensorModeCombo"
                    anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                    width: 224; height: 64
                    model: [{label:"小尺寸合成",value:"sim-small"},{label:"全尺寸合成",value:"sim-full"}]
                    textRole: "label"
                    currentIndex: menu.cameraController && menu.cameraController.sensorMode === "sim-full" ? 1 : 0
                    enabled: menu.settingsEnabled
                    onActivated: menu.cameraController.setSensorMode(model[index].value)
                    background: Rectangle { color: menu.colors.raised; border.color: sensorMode.activeFocus ? menu.colors.accent : menu.colors.line; border.width: sensorMode.activeFocus ? 2 : 1 }
                    contentItem: Text { text: sensorMode.displayText; leftPadding: 12; rightPadding: 28; verticalAlignment: Text.AlignVCenter; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18; opacity: sensorMode.enabled ? 1 : 0.5 }
                    indicator: Text { x: sensorMode.width-24; y: (sensorMode.height-height)/2; text: "v"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                    delegate: ItemDelegate { width: sensorMode.width; height: 64; highlighted: sensorMode.highlightedIndex === index; contentItem: Text { text: qsTr(modelData.label); verticalAlignment: Text.AlignVCenter; color: highlighted ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 } background: Rectangle { color: highlighted ? menu.colors.accent : menu.colors.surface } }
                    popup: Popup { y: sensorMode.height; width: sensorMode.width; padding: 0; implicitHeight: contentItem.implicitHeight; background: Rectangle { color: menu.colors.surface; border.color: menu.colors.line } contentItem: ListView { clip: true; implicitHeight: contentHeight; model: sensorMode.popup.visible ? sensorMode.delegateModel : null; currentIndex: sensorMode.highlightedIndex } }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Repeater {
                model: [{label:"图像尺寸",value:menu.cameraController && menu.cameraController.sensorMode === "sim-full" ? "6064 x 4040" : "128 x 96"},{label:"文件格式",value:"Bayer DNG"},{label:"RAW 有效精度",value:qsTr("14-bit 数值")},{label:"存储容器",value:"16-bit"}]
                Item {
                    width: parent.width; height: 64
                    Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: qsTr(modelData.label); color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                    Text { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: modelData.value; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
                }
            }
            Text { width: parent.width-32; x: 16; topPadding: 16; text: qsTr("当前为合成 Bayer 图像，尚未连接真实传感器。全尺寸合成需要更多处理时间。"); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14; wrapMode: Text.WordWrap }
        }
    }

    Component {
        id: displayPage
        Column {
            Item {
                width: parent.width; height: 64
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: qsTr("构图网格"); color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                Switch {
                    id: gridSwitch
                    objectName: "menuGridSwitch"
                    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                    width: 64; height: 64
                    checked: menu.shell && menu.shell.gridEnabled
                    onToggled: menu.shell.setPreference("grid_enabled", checked)
                    indicator: Rectangle { width: 48; height: 26; x: (gridSwitch.width-width)/2; y: (gridSwitch.height-height)/2; radius: 13; color: gridSwitch.checked ? menu.colors.accent : menu.colors.line; Rectangle { x: gridSwitch.checked ? parent.width-width-3 : 3; anchors.verticalCenter: parent.verticalCenter; width: 20; height: 20; radius: 10; color: "#ffffff" } }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Item {
                width:parent.width;height:88
                Text {x:16;y:12;text:qsTr("电子水平仪");color:menu.colors.text;font.family:menu.uiFont;font.pixelSize:18}
                Text {x:16;y:40;width:parent.width-112;text:typeof levelControl !== "undefined" && levelControl.settingsError ? qsTr("水平仪设置无法保存") : qsTr("没有新鲜姿态数据时显示不可用；演示角度始终标注 DEMO。");color:menu.colors.muted;font.family:menu.uiFont;font.pixelSize:12;wrapMode:Text.WordWrap}
                Switch {
                    id:levelSwitch;objectName:"menuLevelSwitch"
                    anchors.right:parent.right;anchors.rightMargin:14;anchors.verticalCenter:parent.verticalCenter
                    width:64;height:64
                    enabled:typeof levelControl !== "undefined" && levelControl !== null
                    checked:typeof levelControl !== "undefined" && levelControl.enabled
                    onToggled:if(typeof levelControl !== "undefined")levelControl.setEnabled(checked)
                    indicator:Rectangle {width:48;height:26;x:(levelSwitch.width-width)/2;y:(levelSwitch.height-height)/2;radius:13;color:levelSwitch.checked ? menu.colors.accent : menu.colors.line;Rectangle {x:levelSwitch.checked ? parent.width-width-3 : 3;anchors.verticalCenter:parent.verticalCenter;width:20;height:20;radius:10;color:"#ffffff"}}
                }
                Rectangle {anchors.bottom:parent.bottom;width:parent.width;height:1;color:menu.colors.line}
            }
            Item {
                width: parent.width; height: 64
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: qsTr("显示主题"); color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                LocalizedComboBox {
                    id: themeChoice
                    objectName: "menuThemeCombo"
                    anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                    width: 168; height: 64
                    model: [qsTr("深色"), qsTr("浅色")]
                    currentIndex: menu.shell && menu.shell.theme === "light" ? 1 : 0
                    onActivated: menu.shell.setPreference("theme", index === 0 ? "dark" : "light")
                    background: Rectangle { color: menu.colors.raised; border.color: themeChoice.activeFocus ? menu.colors.accent : menu.colors.line; border.width: themeChoice.activeFocus ? 2 : 1 }
                    contentItem: Text { text: themeChoice.displayText; leftPadding: 12; rightPadding: 28; verticalAlignment: Text.AlignVCenter; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                    indicator: Text { x: themeChoice.width-24; y: (themeChoice.height-height)/2; text: "v"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                    delegate: ItemDelegate { width: themeChoice.width; height: 64; highlighted: themeChoice.highlightedIndex === index; contentItem: Text { text: qsTr(modelData); verticalAlignment: Text.AlignVCenter; color: highlighted ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 } background: Rectangle { color: highlighted ? menu.colors.accent : menu.colors.surface } }
                    popup: Popup { y: themeChoice.height; width: themeChoice.width; padding: 0; implicitHeight: contentItem.implicitHeight; background: Rectangle { color: menu.colors.surface; border.color: menu.colors.line } contentItem: ListView { clip: true; implicitHeight: contentHeight; model: themeChoice.popup.visible ? themeChoice.delegateModel : null; currentIndex: themeChoice.highlightedIndex } }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Item {
                width: parent.width; height: 128
                Text { anchors.left: parent.left; anchors.leftMargin: 16; y: 15; text: qsTr("取景显示亮度"); color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                Text { anchors.right: parent.right; anchors.rightMargin: 20; y: 15; text: menu.shell ? menu.shell.previewBrightness + "%" : "100%"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                Slider {
                    id: brightness
                    objectName: "menuBrightnessSlider"
                    anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: 16; anchors.rightMargin: 16
                    y: 48; height: 64
                    from: 25; to: 100; stepSize: 1
                    value: menu.shell ? menu.shell.previewBrightness : 100
                    onMoved: menu.shell.setPreference("preview_brightness", Math.round(value))
                    background: Rectangle { x: brightness.leftPadding; y: brightness.topPadding+brightness.availableHeight/2-height/2; width: brightness.availableWidth; height: 4; color: menu.colors.line; Rectangle { width: brightness.visualPosition*parent.width; height: parent.height; color: menu.colors.accent } }
                    handle: Rectangle { x: brightness.leftPadding+brightness.visualPosition*(brightness.availableWidth-width); y: brightness.topPadding+brightness.availableHeight/2-height/2; width: 24; height: 24; radius: 12; color: menu.colors.text; border.color: menu.colors.accent; border.width: 2 }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Text { width: parent.width-32; x: 16; topPadding: 16; text: qsTr("取景显示亮度 adjusts the displayed image only. It does not control physical screen backlight or saved RAW exposure."); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14; wrapMode: Text.WordWrap }
        }
    }

    Component {
        id: storagePage
        ColumnLayout {
            spacing: 0
            Button {
                id: playbackButton
                objectName: "menuPlaybackButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                onClicked: menu.playbackRequested()
                background: Rectangle { color: playbackButton.down || playbackButton.activeFocus ? menu.colors.accent : menu.colors.background; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                contentItem: RowLayout { Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: qsTr("打开照片回放"); color: playbackButton.down || playbackButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 } Text { Layout.rightMargin: 11; text: ">"; color: playbackButton.down || playbackButton.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 } }
            }
            ScrollView {
                id: storageScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                Column {
                    width: storageScroll.availableWidth
                    Repeater {
                        model: menu.apps ? menu.apps.storageRows : []
                        Item {
                            width: parent.width
                            height: Math.max(64, storageValue.implicitHeight+22, storageLabel.implicitHeight+22)
                            Text { id: storageLabel; x: 16; width: 174; anchors.verticalCenter: parent.verticalCenter; text: menu.rowLabel(modelData.label); textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                            Text { id: storageValue; x: 206; width: Math.max(100,parent.width-x-20); anchors.verticalCenter: parent.verticalCenter; text: menu.rowValue(modelData.value); textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: systemPage
        ColumnLayout {
            spacing: 0
            Button {
                id: resetButton
                objectName: "menuResetPreferencesButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                onClicked: menu.shell.resetPreferences()
                background: Rectangle { color: resetButton.down || resetButton.activeFocus ? menu.colors.accent : menu.colors.background; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                contentItem: RowLayout { Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: qsTr("重置显示偏好"); color: resetButton.down || resetButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 } Text { Layout.rightMargin: 11; text: ">"; color: resetButton.down || resetButton.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 } }
            }
            Item { Layout.fillWidth: true; Layout.preferredHeight: 48; Text { anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16; verticalAlignment: Text.AlignVCenter; text: qsTr("仅重置主题、构图网格和取景显示亮度。"); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14; wrapMode: Text.WordWrap } }
            ScrollView {
                id: systemScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                Column {
                    width: systemScroll.availableWidth
                    Repeater {
                        model: menu.apps ? menu.apps.systemRows : []
                        Item {
                            width: parent.width
                            height: Math.max(64, systemValue.implicitHeight+22, systemLabel.implicitHeight+22)
                            Text { id: systemLabel; x: 16; width: 174; anchors.verticalCenter: parent.verticalCenter; text: menu.rowLabel(modelData.label); textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 }
                            Text { id: systemValue; x: 206; width: Math.max(100,parent.width-x-20); anchors.verticalCenter: parent.verticalCenter; text: menu.rowValue(modelData.value); textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: toolsPage
        Column {
            Repeater {
                model: [{id:"recipes",label:"拍摄配方"},{id:"devices",label:"设备"},{id:"diagnostics",label:"状态诊断"},{id:"system",label:"系统信息"}]
                Button {
                    id: toolButton
                    objectName: "menuTool-" + modelData.id
                    width: parent.width
                    height: 64
                    onClicked: menu.toolRequested(modelData.id)
                    background: Rectangle { color: toolButton.down || toolButton.activeFocus ? menu.colors.accent : menu.colors.background; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                    contentItem: RowLayout { Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: qsTr(modelData.label); color: toolButton.down || toolButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 18 } Text { Layout.rightMargin: 11; text: ">"; color: toolButton.down || toolButton.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 18 } }
                }
            }
            Text { width: parent.width-32; x: 16; topPadding: 16; text: qsTr("离线摄影工具，打开工具不会触发拍摄。"); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14; wrapMode: Text.WordWrap }
        }
    }
}
