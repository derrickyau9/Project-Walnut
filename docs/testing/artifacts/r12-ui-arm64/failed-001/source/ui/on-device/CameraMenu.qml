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
    property bool toolsSelected: false
    signal closeRequested()
    signal quickRequested(string field)
    signal toolRequested(string app)
    signal playbackRequested()

    readonly property var fallbackTheme: ({background:"#17191d",surface:"#202328",raised:"#292d33",layer:"#30353c",text:"#f2f3f5",muted:"#b6bbc4",helper:"#9299a5",line:"#3b414b",accent:"#da3530",softAccent:"#422725"})
    readonly property var colors: theme && theme.background ? theme : fallbackTheme
    readonly property string uiFont: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
    readonly property string groupId: toolsSelected ? "tools" : shell ? shell.currentGroup : "exposure"
    readonly property bool settingsEnabled: cameraController && cameraController.connectionState === "ready" && !cameraController.busy && cameraController.captureState !== "unknown"
    readonly property string menuError: shell && shell.errorText.length > 0 ? shell.errorText : cameraController ? cameraController.errorText : ""
    color: colors.background
    focus: visible

    function categoryTitle(id) {
        if (id === "tools") return "Tools"
        var groups = shell ? shell.settingsGroups : []
        for (var i = 0; i < groups.length; i++)
            if (groups[i].id === id) return groups[i].title
        return "Exposure"
    }
    function chooseCategory(id) {
        if (id === "tools") {
            toolsSelected = true
        } else {
            toolsSelected = false
            if (shell) shell.selectGroup(id)
        }
    }
    function exposureLabel(value) {
        if (!(value > 0)) return "--"
        if (value >= 1000000) return (value / 1000000).toFixed(value % 1000000 ? 1 : 0) + " s"
        var denominator = 1000000 / value
        if (Math.abs(denominator - Math.round(denominator)) < 0.001)
            return "1/" + Math.round(denominator) + " s"
        return value >= 1000 ? (value / 1000).toFixed(1) + " ms" : value + " us"
    }
    onVisibleChanged: if (visible && apps) apps.refreshDiagnostics()
    Component.onCompleted: if (visible && apps) apps.refreshDiagnostics()
    Keys.onEscapePressed: { closeRequested(); event.accepted = true }

    Rectangle {
        id: header
        height: 48
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        color: menu.colors.surface
        Text { anchors.left: parent.left; anchors.leftMargin: 18; anchors.verticalCenter: parent.verticalCenter; text: "MENU"; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 19; font.weight: Font.DemiBold; font.letterSpacing: 1 }
        Button {
            id: closeButton
            objectName: "menuBackButton"
            anchors.right: parent.right
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            width: 156
            height: 44
            onClicked: menu.closeRequested()
            background: Rectangle { color: closeButton.down || closeButton.activeFocus ? menu.colors.accent : "transparent" }
            contentItem: Row {
                spacing: 10
                DaOSIcon { width: 19; height: 19; anchors.verticalCenter: parent.verticalCenter; kind: "back"; ink: closeButton.down || closeButton.activeFocus ? "#ffffff" : menu.colors.text }
                Text { text: "Back to shoot"; anchors.verticalCenter: parent.verticalCenter; color: closeButton.down || closeButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 13 }
            }
        }
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: menu.colors.line }
    }

    Rectangle {
        id: categories
        anchors.left: parent.left
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        width: 144
        color: menu.colors.surface
        Column {
            width: parent.width
            Repeater {
                model: menu.shell ? menu.shell.settingsGroups : []
                Button {
                    id: categoryButton
                    objectName: "menuCategory-" + modelData.id
                    width: categories.width
                    height: 56
                    property bool selected: menu.groupId === modelData.id
                    onClicked: menu.chooseCategory(modelData.id)
                    background: Rectangle { color: categoryButton.selected ? menu.colors.accent : categoryButton.down || categoryButton.activeFocus ? menu.colors.raised : "transparent" }
                    contentItem: Text { text: modelData.title; textFormat: Text.PlainText; leftPadding: 9; verticalAlignment: Text.AlignVCenter; color: categoryButton.selected ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14; font.weight: categoryButton.selected ? Font.DemiBold : Font.Normal }
                }
            }
            Button {
                id: toolsButton
                objectName: "menuCategory-tools"
                width: categories.width
                height: 56
                onClicked: menu.chooseCategory("tools")
                background: Rectangle { color: menu.toolsSelected ? menu.colors.accent : toolsButton.down || toolsButton.activeFocus ? menu.colors.raised : "transparent" }
                contentItem: Text { text: "Tools"; leftPadding: 9; verticalAlignment: Text.AlignVCenter; color: menu.toolsSelected ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14; font.weight: menu.toolsSelected ? Font.DemiBold : Font.Normal }
            }
        }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 1; color: menu.colors.line }
    }

    ColumnLayout {
        id: panel
        anchors.left: categories.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        spacing: 0
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: menu.categoryTitle(menu.groupId).toUpperCase(); color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 11; font.letterSpacing: 1.2 }
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: menu.colors.line }
        }
        Loader {
            id: page
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: menu.groupId === "exposure" ? exposurePage : menu.groupId === "image" ? imagePage : menu.groupId === "display" ? displayPage : menu.groupId === "storage" ? storagePage : menu.groupId === "system" ? systemPage : toolsPage
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 50
            visible: menu.menuError.length > 0
            Rectangle { anchors.fill: parent; color: menu.colors.softAccent }
            Text { anchors.fill: parent; anchors.margins: 10; text: menu.menuError; textFormat: Text.PlainText; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight }
        }
    }

    Component {
        id: exposurePage
        Column {
            Item {
                width: parent.width; height: 52
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Exposure mode"; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 }
                Text { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: "M  Manual"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 15 }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Repeater {
                model: [{field:"exposure",label:"Shutter speed",value:menu.cameraController ? menu.exposureLabel(menu.cameraController.exposureUs) : "--"},{field:"gain",label:"Analogue gain",value:menu.cameraController ? Number(menu.cameraController.analogueGain).toFixed(2) + "x" : "--"}]
                Button {
                    id: exposureRow
                    objectName: modelData.field === "exposure" ? "menuShutterSetting" : "menuGainSetting"
                    width: parent.width
                    height: 56
                    enabled: menu.settingsEnabled
                    onClicked: menu.quickRequested(modelData.field)
                    background: Rectangle { color: exposureRow.down || exposureRow.activeFocus ? menu.colors.accent : "transparent"; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                    contentItem: RowLayout {
                        spacing: 14
                        Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: modelData.label; color: exposureRow.down || exposureRow.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14; opacity: exposureRow.enabled ? 1 : 0.5 }
                        Text { text: modelData.value; color: exposureRow.down || exposureRow.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 16; opacity: exposureRow.enabled ? 1 : 0.5 }
                        Text { Layout.rightMargin: 11; text: ">"; color: exposureRow.down || exposureRow.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 15 }
                    }
                }
            }
            Text { width: parent.width - 32; x: 16; topPadding: 16; text: menu.settingsEnabled ? "Choose shutter speed or gain to adjust the next photograph." : "Exposure controls are paused until the camera is ready and the original capture outcome is known."; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 12; wrapMode: Text.WordWrap }
        }
    }

    Component {
        id: imagePage
        Column {
            Item {
                width: parent.width; height: 56
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Simulation mode"; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 }
                ComboBox {
                    id: sensorMode
                    objectName: "menuSensorModeCombo"
                    anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                    width: 224; height: 44
                    model: [{label:"Simulation Small",value:"sim-small"},{label:"Simulation Full",value:"sim-full"}]
                    textRole: "label"
                    currentIndex: menu.cameraController && menu.cameraController.sensorMode === "sim-full" ? 1 : 0
                    enabled: menu.settingsEnabled
                    onActivated: menu.cameraController.setSensorMode(model[index].value)
                    background: Rectangle { color: menu.colors.raised; border.color: sensorMode.activeFocus ? menu.colors.accent : menu.colors.line; border.width: sensorMode.activeFocus ? 2 : 1 }
                    contentItem: Text { text: sensorMode.displayText; leftPadding: 12; rightPadding: 28; verticalAlignment: Text.AlignVCenter; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 13; opacity: sensorMode.enabled ? 1 : 0.5 }
                    indicator: Text { x: sensorMode.width-24; y: (sensorMode.height-height)/2; text: "v"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 13 }
                    delegate: ItemDelegate { width: sensorMode.width; height: 44; highlighted: sensorMode.highlightedIndex === index; contentItem: Text { text: modelData.label; verticalAlignment: Text.AlignVCenter; color: highlighted ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 13 } background: Rectangle { color: highlighted ? menu.colors.accent : menu.colors.surface } }
                    popup: Popup { y: sensorMode.height; width: sensorMode.width; padding: 0; implicitHeight: contentItem.implicitHeight; background: Rectangle { color: menu.colors.surface; border.color: menu.colors.line } contentItem: ListView { clip: true; implicitHeight: contentHeight; model: sensorMode.popup.visible ? sensorMode.delegateModel : null; currentIndex: sensorMode.highlightedIndex } }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Repeater {
                model: [{label:"Image size",value:menu.cameraController && menu.cameraController.sensorMode === "sim-full" ? "6064 x 4040" : "128 x 96"},{label:"File format",value:"Bayer DNG"},{label:"RAW precision",value:"14-bit values"},{label:"Storage container",value:"16-bit"}]
                Item {
                    width: parent.width; height: 52
                    Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 }
                    Text { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: modelData.value; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14 }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
                }
            }
            Text { width: parent.width-32; x: 16; topPadding: 16; text: "Synthetic Bayer images. No physical sensor is connected. Full-size simulation can take additional processing time."; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 12; wrapMode: Text.WordWrap }
        }
    }

    Component {
        id: displayPage
        Column {
            Item {
                width: parent.width; height: 56
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Framing grid"; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 }
                Switch {
                    id: gridSwitch
                    objectName: "menuGridSwitch"
                    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                    width: 62; height: 44
                    checked: menu.shell && menu.shell.gridEnabled
                    onToggled: menu.shell.setPreference("grid_enabled", checked)
                    indicator: Rectangle { width: 48; height: 26; x: (gridSwitch.width-width)/2; y: (gridSwitch.height-height)/2; radius: 13; color: gridSwitch.checked ? menu.colors.accent : menu.colors.line; Rectangle { x: gridSwitch.checked ? parent.width-width-3 : 3; anchors.verticalCenter: parent.verticalCenter; width: 20; height: 20; radius: 10; color: "#ffffff" } }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Item {
                width: parent.width; height: 56
                Text { anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Theme"; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 }
                ComboBox {
                    id: themeChoice
                    objectName: "menuThemeCombo"
                    anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                    width: 168; height: 44
                    model: ["Dark", "Light"]
                    currentIndex: menu.shell && menu.shell.theme === "light" ? 1 : 0
                    onActivated: menu.shell.setPreference("theme", index === 0 ? "dark" : "light")
                    background: Rectangle { color: menu.colors.raised; border.color: themeChoice.activeFocus ? menu.colors.accent : menu.colors.line; border.width: themeChoice.activeFocus ? 2 : 1 }
                    contentItem: Text { text: themeChoice.displayText; leftPadding: 12; rightPadding: 28; verticalAlignment: Text.AlignVCenter; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 13 }
                    indicator: Text { x: themeChoice.width-24; y: (themeChoice.height-height)/2; text: "v"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 13 }
                    delegate: ItemDelegate { width: themeChoice.width; height: 44; highlighted: themeChoice.highlightedIndex === index; contentItem: Text { text: modelData; verticalAlignment: Text.AlignVCenter; color: highlighted ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 13 } background: Rectangle { color: highlighted ? menu.colors.accent : menu.colors.surface } }
                    popup: Popup { y: themeChoice.height; width: themeChoice.width; padding: 0; implicitHeight: contentItem.implicitHeight; background: Rectangle { color: menu.colors.surface; border.color: menu.colors.line } contentItem: ListView { clip: true; implicitHeight: contentHeight; model: themeChoice.popup.visible ? themeChoice.delegateModel : null; currentIndex: themeChoice.highlightedIndex } }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Item {
                width: parent.width; height: 102
                Text { anchors.left: parent.left; anchors.leftMargin: 16; y: 15; text: "Preview brightness"; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 }
                Text { anchors.right: parent.right; anchors.rightMargin: 20; y: 15; text: menu.shell ? menu.shell.previewBrightness + "%" : "100%"; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 14 }
                Slider {
                    id: brightness
                    objectName: "menuBrightnessSlider"
                    anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: 16; anchors.rightMargin: 16
                    y: 44; height: 44
                    from: 25; to: 100; stepSize: 1
                    value: menu.shell ? menu.shell.previewBrightness : 100
                    onMoved: menu.shell.setPreference("preview_brightness", Math.round(value))
                    background: Rectangle { x: brightness.leftPadding; y: brightness.topPadding+brightness.availableHeight/2-height/2; width: brightness.availableWidth; height: 4; color: menu.colors.line; Rectangle { width: brightness.visualPosition*parent.width; height: parent.height; color: menu.colors.accent } }
                    handle: Rectangle { x: brightness.leftPadding+brightness.visualPosition*(brightness.availableWidth-width); y: brightness.topPadding+brightness.availableHeight/2-height/2; width: 24; height: 24; radius: 12; color: menu.colors.text; border.color: menu.colors.accent; border.width: 2 }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line }
            }
            Text { width: parent.width-32; x: 16; topPadding: 16; text: "Preview brightness adjusts the displayed image only. It does not control physical screen backlight or saved RAW exposure."; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 12; wrapMode: Text.WordWrap }
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
                Layout.preferredHeight: 52
                onClicked: menu.playbackRequested()
                background: Rectangle { color: playbackButton.down || playbackButton.activeFocus ? menu.colors.accent : "transparent"; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                contentItem: RowLayout { Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: "Open playback"; color: playbackButton.down || playbackButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 } Text { Layout.rightMargin: 11; text: ">"; color: playbackButton.down || playbackButton.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 15 } }
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
                            height: Math.max(52, storageValue.implicitHeight+22, storageLabel.implicitHeight+22)
                            Text { id: storageLabel; x: 16; width: 174; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 12 }
                            Text { id: storageValue; x: 206; width: Math.max(100,parent.width-x-20); anchors.verticalCenter: parent.verticalCenter; text: modelData.value; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 12 }
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
                Layout.preferredHeight: 52
                onClicked: menu.shell.resetPreferences()
                background: Rectangle { color: resetButton.down || resetButton.activeFocus ? menu.colors.accent : "transparent"; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                contentItem: RowLayout { Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: "Reset display preferences"; color: resetButton.down || resetButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 } Text { Layout.rightMargin: 11; text: ">"; color: resetButton.down || resetButton.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 15 } }
            }
            Item { Layout.fillWidth: true; Layout.preferredHeight: 42; Text { anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16; verticalAlignment: Text.AlignVCenter; text: "Reset affects theme, grid and preview brightness only."; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 11; wrapMode: Text.WordWrap } }
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
                            height: Math.max(52, systemValue.implicitHeight+22, systemLabel.implicitHeight+22)
                            Text { id: systemLabel; x: 16; width: 174; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 12 }
                            Text { id: systemValue; x: 206; width: Math.max(100,parent.width-x-20); anchors.verticalCenter: parent.verticalCenter; text: modelData.value; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; color: menu.colors.text; font.family: menu.uiFont; font.pixelSize: 12 }
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
                model: [{id:"recipes",label:"Recipes"},{id:"devices",label:"Devices"},{id:"diagnostics",label:"Diagnostics"},{id:"system",label:"System information"}]
                Button {
                    id: toolButton
                    objectName: "menuTool-" + modelData.id
                    width: parent.width
                    height: 56
                    onClicked: menu.toolRequested(modelData.id)
                    background: Rectangle { color: toolButton.down || toolButton.activeFocus ? menu.colors.accent : "transparent"; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: menu.colors.line } }
                    contentItem: RowLayout { Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: modelData.label; color: toolButton.down || toolButton.activeFocus ? "#ffffff" : menu.colors.text; font.family: menu.uiFont; font.pixelSize: 14 } Text { Layout.rightMargin: 11; text: ">"; color: toolButton.down || toolButton.activeFocus ? "#ffffff" : menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 15 } }
                }
            }
            Text { width: parent.width-32; x: 16; topPadding: 16; text: "Offline camera tools. Opening a tool does not submit a photograph."; color: menu.colors.muted; font.family: menu.uiFont; font.pixelSize: 12; wrapMode: Text.WordWrap }
        }
    }
}
