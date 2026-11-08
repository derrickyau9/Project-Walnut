import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: workspace
    property var shell
    property var cameraController
    property var apps
    property var theme
    property string previousApp: ""
    readonly property var fallbackTheme: ({background:"#ffffff",surface:"#ffffff",raised:"#eeeeee",layer:"#dddddd",text:"#000000",muted:"#444444",helper:"#555555",line:"#cccccc",accent:"#666666",softAccent:"#eeeeee",success:"#666666",error:"#666666"})
    readonly property var colors: theme && theme.surface ? theme : theme && theme.light ? (shell && shell.theme === "dark" ? theme.dark : theme.light) : fallbackTheme
    readonly property bool darkMode: shell && shell.theme === "dark"
    readonly property string currentApp: shell ? shell.currentApp : "settings"
    readonly property bool appNoticeMatches: !!apps && apps.messageScope === currentApp
    readonly property bool settingsEnabled: cameraController && cameraController.connectionState === "ready" && !cameraController.busy && cameraController.captureState !== "unknown"
    readonly property string uiFont: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
    readonly property string monoFont: Qt.platform.os === "windows" ? "Consolas" : "monospace"

    function refreshCurrent() {
        if (currentApp === "photos" && cameraController)
            cameraController.refreshLibrary()
        else if (currentApp === "recipes" && apps)
            apps.refreshRecipes()
        else if (apps)
            apps.refreshDiagnostics()
    }
    function observeNavigation() {
        if (currentApp !== previousApp) {
            previousApp = currentApp
            if (currentApp !== "camera")
                refreshCurrent()
        }
    }
    onCurrentAppChanged: observeNavigation()
    Component.onCompleted: { previousApp = ""; observeNavigation() }

    
    
    
    
    
    
    
    

    ColumnLayout {
        anchors.fill: parent
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: 50
            spacing: 12
            visible: workspace.currentApp !== "recipes" && workspace.currentApp !== "photos"
            Rectangle {
                implicitWidth: 42; implicitHeight: 42; radius: 14; color: workspace.colors.softAccent
                DaOSIcon { anchors.centerIn: parent; width: 23; height: 23; kind: workspace.currentApp; ink: workspace.colors.accent }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Text { font.family: workspace.uiFont;  text: qsTr("DaOS / CAMERA WORKSPACE"); color: workspace.colors.helper; font.pixelSize: 10; font.letterSpacing: 1.2 }
                Text { font.family: workspace.uiFont;  text: workspace.shell ? workspace.shell.appTitle : qsTr("Settings"); color: workspace.colors.text; font.pixelSize: 25; font.weight: Font.DemiBold }
            }
            MediaButton {
                visible: workspace.currentApp === "settings" || workspace.currentApp === "system" || workspace.currentApp === "devices"
                text: qsTr("能力 / 性能")
                onClicked: capabilityPopup.open()
            }
            Button {
        id: workspaceControl1_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl1_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl1_action.enabled ? workspace.colors.raised : workspaceControl1_action.emphasized ? workspace.colors.accent : workspaceControl1_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl1_action.activeFocus ? 2 : 1
            border.color: workspaceControl1_action.activeFocus || workspaceControl1_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl1_actionText
            text: workspaceControl1_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl1_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl1_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl1_action.enabled ? workspace.colors.helper : workspaceControl1_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appWorkspaceBackButton"; text: qsTr("Back"); enabled: workspace.shell && workspace.shell.canGoBack; onClicked: workspace.shell.back() }
            Button {
        id: workspaceControl2_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl2_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl2_action.enabled ? workspace.colors.raised : workspaceControl2_action.emphasized ? workspace.colors.accent : workspaceControl2_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl2_action.activeFocus ? 2 : 1
            border.color: workspaceControl2_action.activeFocus || workspaceControl2_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl2_actionText
            text: workspaceControl2_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl2_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl2_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl2_action.enabled ? workspace.colors.helper : workspaceControl2_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appWorkspaceRefreshButton"; text: qsTr("Refresh"); onClicked: workspace.refreshCurrent() }
        }
        Frame {
            Layout.fillWidth: true
            visible: (workspace.shell && workspace.shell.errorText.length > 0) || (workspace.appNoticeMatches && workspace.apps.errorText.length > 0)
            padding: 12
            background: Rectangle { color: workspace.colors.softAccent; radius: 12; border.color: workspace.colors.accent }
            contentItem: Text { font.family: workspace.uiFont;  text: workspace.shell && workspace.shell.errorText.length > 0 ? qsTr(workspace.shell.errorText) : workspace.appNoticeMatches ? qsTr(workspace.apps.errorText) : ""; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 12; wrapMode: Text.WordWrap }
        }
        Loader {
            id: pageLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: workspace.currentApp === "photos" ? photosPage : workspace.currentApp === "settings" ? settingsPage : workspace.currentApp === "recipes" ? recipesPage : workspace.currentApp === "devices" ? devicesPage : workspace.currentApp === "diagnostics" ? diagnosticsPage : workspace.currentApp === "system" ? systemPage : null
        }
    }

    Component {
        id: photosPage
        CameraPlayback { cameraController: workspace.cameraController; apps: workspace.apps; theme: workspace.colors; onCloseRequested: workspace.shell.navigate("camera") }
    }
    Component {
        id: recipesPage
        RecipeWorkspace { apps: workspace.apps; cameraController: workspace.cameraController; onCloseRequested: workspace.shell.navigate("camera") }
    }
    Popup {
        id: capabilityPopup
        parent: workspace; x: 0; y: 0; width: workspace.width; height: workspace.height
        modal: true; focus: true; padding: 0
        contentItem: CapabilityPage { cameraController: workspace.cameraController; onCloseRequested: capabilityPopup.close() }
        background: Rectangle { color: "#111111" }
    }
    Component {
        id: legacyPhotosPage
        RowLayout {
            spacing: 14
            Frame {
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                Layout.preferredWidth: Math.min(290, Math.max(205, workspace.width * 0.32))
                Layout.fillHeight: true
                padding: 12
                contentItem: ColumnLayout {
                    spacing: 10
                    Text { font.family: workspace.uiFont;  text: qsTr("COMMITTED CAPTURES"); color: workspace.colors.helper; font.pixelSize: 10; font.letterSpacing: 1; Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
        id: workspaceControl4_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl4_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl4_action.enabled ? workspace.colors.raised : workspaceControl4_action.emphasized ? workspace.colors.accent : workspaceControl4_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl4_action.activeFocus ? 2 : 1
            border.color: workspaceControl4_action.activeFocus || workspaceControl4_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl4_actionText
            text: workspaceControl4_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl4_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl4_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl4_action.enabled ? workspace.colors.helper : workspaceControl4_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appsPhotoRefreshButton"; Layout.fillWidth: true; text: qsTr("Newest"); enabled: workspace.cameraController && workspace.cameraController.canBrowseCaptures && !workspace.cameraController.libraryLoading; onClicked: workspace.cameraController.refreshLibrary() }
                        Button {
        id: workspaceControl5_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl5_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl5_action.enabled ? workspace.colors.raised : workspaceControl5_action.emphasized ? workspace.colors.accent : workspaceControl5_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl5_action.activeFocus ? 2 : 1
            border.color: workspaceControl5_action.activeFocus || workspaceControl5_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl5_actionText
            text: workspaceControl5_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl5_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl5_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl5_action.enabled ? workspace.colors.helper : workspaceControl5_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appsPhotoOlderButton"; Layout.fillWidth: true; text: qsTr("Older"); enabled: workspace.cameraController && workspace.cameraController.libraryNextCursor.length > 0 && !workspace.cameraController.libraryLoading; onClicked: workspace.cameraController.nextLibraryPage() }
                    }
                    ListView {
                        id: photoList
                        objectName: "appsPhotoList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 7
                        model: workspace.cameraController ? workspace.cameraController.libraryItems : []
                        delegate: Button {
                            id: photoButton
                            objectName: "appsPhotoItem-" + modelData.request_id
                            width: photoList.width
                            height: 82
                            enabled: !workspace.cameraController.libraryLoading
                            property bool selected: workspace.cameraController.selectedCaptureId === modelData.request_id
                            onClicked: workspace.cameraController.selectCapture(modelData.request_id)
                            background: Rectangle { radius: 12; color: photoButton.selected ? workspace.colors.softAccent : workspace.colors.raised; border.color: photoButton.selected ? workspace.colors.accent : workspace.colors.line }
                            contentItem: ColumnLayout {
                                spacing: 5
                                Text { Layout.fillWidth: true; text: modelData.filename; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 11; font.family: workspace.monoFont; elide: Text.ElideMiddle }
                                Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.width + " x " + modelData.height + "  |  " + modelData.actual_exposure_us + qsTr(" us"); color: workspace.colors.helper; font.pixelSize: 11; elide: Text.ElideRight }
                                Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: qsTr("Actual gain ") + Number(modelData.actual_analogue_gain).toFixed(2) + "x"; color: workspace.colors.helper; font.pixelSize: 11 }
                            }
                        }
                        Text { font.family: workspace.uiFont;  anchors.centerIn: parent; width: parent.width - 20; visible: photoList.count === 0; text: workspace.cameraController && workspace.cameraController.libraryLoading ? qsTr("Reading retained journal...") : qsTr("No committed photographs on this page."); color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
                    }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.cameraController ? workspace.cameraController.libraryMessage : qsTr("Camera is unavailable.") }
                }
            }
            Frame {
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 14
                contentItem: ColumnLayout {
                    spacing: 10
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Retained photograph") }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 100
                        color: workspace.colors.raised
                        radius: 12
                        border.color: workspace.colors.line
                        Image { objectName: "appsPhotoReviewImage"; anchors.fill: parent; anchors.margins: 6; source: workspace.cameraController ? workspace.cameraController.reviewUrl : ""; fillMode: Image.PreserveAspectFit; cache: false }
                        Text { font.family: workspace.uiFont;  anchors.centerIn: parent; width: parent.width - 30; visible: !workspace.cameraController || workspace.cameraController.reviewUrl.length === 0; text: workspace.cameraController && workspace.cameraController.selectedCaptureId.length > 0 ? qsTr("Reading saved DNG display conversion...") : qsTr("Select a saved photograph."); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                    }
                    Text { Layout.fillWidth: true; text: workspace.cameraController ? workspace.cameraController.selectedCaptureInfo : ""; textFormat: Text.PlainText; color: workspace.colors.text; font.family: workspace.monoFont; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; visible: text.length > 0 }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Saved DNG display conversion. Synthetic, uncalibrated; separate from live preview.") }
                    TextField {
        id: photoDestination
        implicitHeight: 44
        color: workspace.colors.text
        placeholderTextColor: workspace.colors.helper
        font.pixelSize: 13
        selectByMouse: true
        leftPadding: 12
        rightPadding: 12
        background: Rectangle {
            radius: 12
            color: workspace.colors.raised
            border.width: photoDestination.activeFocus ? 2 : 1
            border.color: photoDestination.activeFocus ? workspace.colors.accent : workspace.colors.line
        }
    
 objectName: "appsPhotoExportPathInput"; Layout.fillWidth: true; placeholderText: qsTr("New absolute .dng path, or leave blank for local exports") }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.cameraController ? workspace.cameraController.exportMessage : ""; visible: text.length > 0 }
                        Item { Layout.fillWidth: true; visible: !workspace.cameraController || workspace.cameraController.exportMessage.length === 0 }
                        Button {
        id: workspaceControl12_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl12_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl12_action.enabled ? workspace.colors.raised : workspaceControl12_action.emphasized ? workspace.colors.accent : workspaceControl12_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl12_action.activeFocus ? 2 : 1
            border.color: workspaceControl12_action.activeFocus || workspaceControl12_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl12_actionText
            text: workspaceControl12_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl12_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl12_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl12_action.enabled ? workspace.colors.helper : workspaceControl12_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appsPhotoExportButton"; text: qsTr("Export DNG"); emphasized: true; enabled: workspace.cameraController && workspace.cameraController.selectedCaptureId.length > 0 && !workspace.cameraController.libraryLoading; onClicked: { if (photoDestination.text.trim().length === 0) photoDestination.text = workspace.apps.exportDirectory + "/capture-" + Date.now() + ".dng"; workspace.cameraController.exportSelected(photoDestination.text) } }
                    }
                }
            }
        }
    }

    Component {
        id: settingsPage
        RowLayout {
            spacing: 14
            Frame {
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                Layout.preferredWidth: workspace.width < 760 ? 145 : 180
                Layout.fillHeight: true
                padding: 8
                contentItem: ColumnLayout {
                    spacing: 7
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; Layout.margins: 9; text: qsTr("CAMERA SETTINGS"); font.pixelSize: 10; font.letterSpacing: 0.7; color: workspace.colors.helper; wrapMode: Text.WordWrap }
                    Repeater {
                        model: workspace.shell ? workspace.shell.settingsGroups : []
                        Button {
        id: workspaceControl14_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl14_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl14_action.enabled ? workspace.colors.raised : workspaceControl14_action.emphasized ? workspace.colors.accent : workspaceControl14_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl14_action.activeFocus ? 2 : 1
            border.color: workspaceControl14_action.activeFocus || workspaceControl14_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl14_actionText
            text: workspaceControl14_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl14_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl14_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl14_action.enabled ? workspace.colors.helper : workspaceControl14_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    

                            objectName: "appSettingsGroup-" + modelData.id
                            Layout.fillWidth: true
                            text: qsTr(modelData.title)
                            emphasized: workspace.shell.currentGroup === modelData.id
                            onClicked: workspace.shell.selectGroup(modelData.id)
                        }
                    }
                    Item { Layout.fillHeight: true }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 Layout.margins: 9; text: qsTr("HF-Sim\nManual exposure\nBayer DNG") }
                }
            }
            ScrollView {
                id: settingScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                Loader {
                    width: settingScroll.availableWidth
                    height: item ? item.implicitHeight : 0
                    sourceComponent: !workspace.shell || workspace.shell.currentGroup === "exposure" ? exposureSettings : workspace.shell.currentGroup === "image" ? imageSettings : workspace.shell.currentGroup === "display" ? displaySettings : workspace.shell.currentGroup === "storage" ? storageSettings : systemSettings
                }
            }
        }
    }
    Component {
        id: exposureSettings
        ColumnLayout {
            spacing: 14
            Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                Layout.fillWidth: true
                contentItem: ColumnLayout {
                    spacing: 15
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Manual exposure") }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Requested settings are applied by the camera authority. Actual per-frame values are recorded separately in each saved photograph.") }
                    Text { font.family: workspace.uiFont;  text: qsTr("Requested exposure (us)"); color: workspace.colors.muted; font.pixelSize: 12 }
                    SpinBox {
        id: workspaceControl19_numeric
        editable: true
        implicitWidth: 220
        implicitHeight: 44
        leftPadding: 38
        rightPadding: 38
        font.pixelSize: 14
        opacity: 1
        background: Rectangle { color: workspace.colors.raised; radius: 12; border.color: workspaceControl19_numeric.activeFocus ? workspace.colors.accent : workspace.colors.line; border.width: workspaceControl19_numeric.activeFocus ? 2 : 1 }
        contentItem: TextInput {
            text: workspaceControl19_numeric.textFromValue(workspaceControl19_numeric.value, workspaceControl19_numeric.locale)
            font.pixelSize: workspaceControl19_numeric.font.pixelSize
            font.family: workspace.uiFont
            color: workspace.colors.text
            selectionColor: workspace.colors.accent
            selectedTextColor: "#ffffff"
            horizontalAlignment: Qt.AlignHCenter
            verticalAlignment: Qt.AlignVCenter
            readOnly: !workspaceControl19_numeric.editable
            validator: workspaceControl19_numeric.validator
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            selectByMouse: true
            clip: true
        }
        up.indicator: Rectangle {
            x: workspaceControl19_numeric.width - width
            width: 36
            height: workspaceControl19_numeric.height
            color: workspace.colors.raised
            Text { font.family: workspace.uiFont;  anchors.centerIn: parent; text: "+"; font.pixelSize: 20; color: workspace.colors.text; opacity: workspaceControl19_numeric.up.enabled ? 1 : 0.3 }
        }
        down.indicator: Rectangle {
            width: 36
            height: workspaceControl19_numeric.height
            color: workspace.colors.raised
            Text { font.family: workspace.uiFont;  anchors.centerIn: parent; text: "-"; font.pixelSize: 20; color: workspace.colors.text; opacity: workspaceControl19_numeric.down.enabled ? 1 : 0.3 }
        }
    
 objectName: "appSettingsExposureInput"; Layout.fillWidth: true; from: 100; to: 30000000; stepSize: 100; value: workspace.cameraController ? workspace.cameraController.exposureUs : 10000; enabled: workspace.settingsEnabled; onValueModified: workspace.cameraController.setExposureUs(value) }
                    Text { font.family: workspace.uiFont;  text: qsTr("Requested analogue gain (x)"); color: workspace.colors.muted; font.pixelSize: 12 }
                    SpinBox {
        id: workspaceControl20_numeric
        editable: true
        implicitWidth: 220
        implicitHeight: 44
        leftPadding: 38
        rightPadding: 38
        font.pixelSize: 14
        opacity: 1
        background: Rectangle { color: workspace.colors.raised; radius: 12; border.color: workspaceControl20_numeric.activeFocus ? workspace.colors.accent : workspace.colors.line; border.width: workspaceControl20_numeric.activeFocus ? 2 : 1 }
        contentItem: TextInput {
            text: workspaceControl20_numeric.textFromValue(workspaceControl20_numeric.value, workspaceControl20_numeric.locale)
            font.pixelSize: workspaceControl20_numeric.font.pixelSize
            font.family: workspace.uiFont
            color: workspace.colors.text
            selectionColor: workspace.colors.accent
            selectedTextColor: "#ffffff"
            horizontalAlignment: Qt.AlignHCenter
            verticalAlignment: Qt.AlignVCenter
            readOnly: !workspaceControl20_numeric.editable

            inputMethodHints: Qt.ImhFormattedNumbersOnly
            selectByMouse: true
            clip: true
        }
        up.indicator: Rectangle {
            x: workspaceControl20_numeric.width - width
            width: 36
            height: workspaceControl20_numeric.height
            color: workspace.colors.raised
            Text { font.family: workspace.uiFont;  anchors.centerIn: parent; text: "+"; font.pixelSize: 20; color: workspace.colors.text; opacity: workspaceControl20_numeric.up.enabled ? 1 : 0.3 }
        }
        down.indicator: Rectangle {
            width: 36
            height: workspaceControl20_numeric.height
            color: workspace.colors.raised
            Text { font.family: workspace.uiFont;  anchors.centerIn: parent; text: "-"; font.pixelSize: 20; color: workspace.colors.text; opacity: workspaceControl20_numeric.down.enabled ? 1 : 0.3 }
        }
    

                        objectName: "appSettingsGainInput"
                        Layout.fillWidth: true
                        from: 100; to: 1600; stepSize: 5
                        value: workspace.cameraController ? Math.round(workspace.cameraController.analogueGain * 100) : 100
                        enabled: workspace.settingsEnabled
                        validator: DoubleValidator { bottom: 1; top: 16; decimals: 2; notation: DoubleValidator.StandardNotation }
                        textFromValue: function(value, locale) { return (value / 100).toFixed(2) }
                        valueFromText: function(text, locale) { return Math.round(Number(text) * 100) }
                        onValueModified: workspace.cameraController.setAnalogueGain(value / 100)
                    }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Parameter editing is paused during capture, disconnection or an uncertain original request."); visible: !workspace.settingsEnabled }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
        id: workspaceControl22_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl22_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl22_action.enabled ? workspace.colors.raised : workspaceControl22_action.emphasized ? workspace.colors.accent : workspaceControl22_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl22_action.activeFocus ? 2 : 1
            border.color: workspaceControl22_action.activeFocus || workspaceControl22_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl22_actionText
            text: workspaceControl22_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl22_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl22_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl22_action.enabled ? workspace.colors.helper : workspaceControl22_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 text: qsTr("Image mode"); onClicked: workspace.shell.selectGroup("image") }
                        Button {
        id: workspaceControl23_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl23_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl23_action.enabled ? workspace.colors.raised : workspaceControl23_action.emphasized ? workspace.colors.accent : workspaceControl23_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl23_action.activeFocus ? 2 : 1
            border.color: workspaceControl23_action.activeFocus || workspaceControl23_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl23_actionText
            text: workspaceControl23_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl23_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl23_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl23_action.enabled ? workspace.colors.helper : workspaceControl23_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 text: qsTr("Return to camera"); emphasized: true; onClicked: workspace.shell.navigate("camera") }
                    }
                }
            }
            Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 10; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Capture identity") } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.cameraController && workspace.cameraController.lastRequestId.length > 0 ? workspace.cameraController.lastRequestId : qsTr("No photograph has been submitted in this session.") } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Changing a menu or setting does not submit a photograph. An uncertain capture is queried using its original request ID.") } } }
        }
    }
    Component {
        id: imageSettings
        ColumnLayout {
            spacing: 14
            Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                Layout.fillWidth: true
                contentItem: ColumnLayout {
                    spacing: 15
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Image mode and RAW") }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("HF-Sim generates synthetic Bayer data. Mode dimensions are simulator settings, not a connected sensor specification.") }
                    LocalizedComboBox {
        id: workspaceControl31_modeField
        objectName: "appSettingsModeCombo"
        implicitHeight: 44
        implicitWidth: 240
        model: ["sim-small", "sim-full"]
        currentIndex: workspace.cameraController && workspace.cameraController.sensorMode === "sim-full" ? 1 : 0
        enabled: workspace.settingsEnabled
        onActivated: workspace.cameraController.setSensorMode(currentText)
        opacity: 1
        background: Rectangle { color: workspace.colors.raised; radius: 12; border.color: workspaceControl31_modeField.activeFocus ? workspace.colors.accent : workspace.colors.line }
        contentItem: Text { font.family: workspace.uiFont;  leftPadding: 12; rightPadding: 32; text: workspaceControl31_modeField.displayText; textFormat: Text.PlainText; color: workspace.colors.text; verticalAlignment: Text.AlignVCenter; font.pixelSize: 14 }
        indicator: Text { font.family: workspace.uiFont;  x: workspaceControl31_modeField.width - 28; y: (workspaceControl31_modeField.height - height)/2; text: "v"; color: workspace.colors.helper; font.pixelSize: 12 }
        delegate: ItemDelegate { width: workspaceControl31_modeField.width; text: qsTr(modelData); highlighted: workspaceControl31_modeField.highlightedIndex === index; contentItem: Text { font.family: workspace.uiFont;  text: qsTr(modelData); color: workspace.colors.text; verticalAlignment: Text.AlignVCenter } background: Rectangle { color: highlighted ? workspace.colors.softAccent : workspace.colors.surface } }
        popup: Popup { y: workspaceControl31_modeField.height + 4; width: workspaceControl31_modeField.width; padding: 6; implicitHeight: contentItem.implicitHeight + 12; background: Rectangle { color: workspace.colors.surface; radius: 12; border.color: workspace.colors.line } contentItem: ListView { clip: true; implicitHeight: contentHeight; model: workspaceControl31_modeField.popup.visible ? workspaceControl31_modeField.delegateModel : null; currentIndex: workspaceControl31_modeField.highlightedIndex } }
    
 Layout.fillWidth: true }
                    ColumnLayout {
        property var rows
        spacing: 0
        Repeater {
            model: parent.rows
            delegate: ColumnLayout {

                spacing: 6
                RowLayout {

                    Layout.topMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 16
                    Text { font.family: workspace.uiFont;  Layout.preferredWidth: Math.min(180, workspace.width * 0.25); Layout.alignment: Qt.AlignTop; text: qsTr(modelData.label); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.value; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: workspace.colors.line; opacity: 1 }
            }
        }
    
 Layout.fillWidth: true; rows: [{label:"Synthetic output",value:workspace.cameraController && workspace.cameraController.sensorMode === "sim-full" ? "6064 x 4040 pixels" : "128 x 96 pixels",tone:"muted"},{label:"File format",value:"Bayer DNG",tone:"muted"},{label:"Sample representation",value:"14-bit synthetic values in 16-bit containers",tone:"muted"},{label:"User styles / LUT",value:"Not baked into RAW",tone:"muted"}] }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("The full-size synthetic mode can use substantial CPU time and memory. It is not a camera throughput benchmark.") }
                }
            }
        }
    }
    Component {
        id: displaySettings
        ColumnLayout {
            spacing: 14
            Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                Layout.fillWidth: true
                contentItem: ColumnLayout {
                    spacing: 16
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Display and framing") }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("These preferences affect the local interface and preview. They do not change exposure, saved RAW pixels or physical screen backlight.") }
                    Switch {
                        id: gridSwitch
                        objectName: "appGridSwitch"
                        Layout.fillWidth: true
                        text: qsTr("Framing grid")
                        checked: workspace.shell && workspace.shell.gridEnabled
                        onToggled: workspace.shell.setPreference("grid_enabled", checked)
                        indicator: Rectangle { implicitWidth: 46; implicitHeight: 26; x: gridSwitch.leftPadding; y: (gridSwitch.height-height)/2; radius: 13; color: gridSwitch.checked ? workspace.colors.accent : workspace.colors.line; Rectangle { x: gridSwitch.checked ? parent.width-width-3 : 3; anchors.verticalCenter: parent.verticalCenter; width: 20; height: 20; radius: 10; color: "#ffffff" } }
                        contentItem: Text { font.family: workspace.uiFont;  text: gridSwitch.text; font.pixelSize: 14; color: workspace.colors.text; leftPadding: 60; verticalAlignment: Text.AlignVCenter }
                    }
                    Text { font.family: workspace.uiFont;  text: qsTr("Appearance"); color: workspace.colors.muted; font.pixelSize: 12 }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
        id: workspaceControl37_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl37_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl37_action.enabled ? workspace.colors.raised : workspaceControl37_action.emphasized ? workspace.colors.accent : workspaceControl37_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl37_action.activeFocus ? 2 : 1
            border.color: workspaceControl37_action.activeFocus || workspaceControl37_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl37_actionText
            text: workspaceControl37_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl37_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl37_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl37_action.enabled ? workspace.colors.helper : workspaceControl37_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appThemeLightButton"; Layout.fillWidth: true; text: qsTr("Light"); emphasized: workspace.shell && workspace.shell.theme === "light"; onClicked: workspace.shell.setPreference("theme", "light") }
                        Button {
        id: workspaceControl38_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl38_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl38_action.enabled ? workspace.colors.raised : workspaceControl38_action.emphasized ? workspace.colors.accent : workspaceControl38_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl38_action.activeFocus ? 2 : 1
            border.color: workspaceControl38_action.activeFocus || workspaceControl38_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl38_actionText
            text: workspaceControl38_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl38_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl38_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl38_action.enabled ? workspace.colors.helper : workspaceControl38_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appThemeDarkButton"; Layout.fillWidth: true; text: qsTr("Dark"); emphasized: workspace.shell && workspace.shell.theme === "dark"; onClicked: workspace.shell.setPreference("theme", "dark") }
                    }
                    RowLayout { Layout.fillWidth: true; Text { font.family: workspace.uiFont;  text: qsTr("Preview brightness - interface only"); color: workspace.colors.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap } Text { font.family: workspace.uiFont;  text: workspace.shell ? workspace.shell.previewBrightness + "%" : "100%"; color: workspace.colors.text; font.pixelSize: 13 } }
                    Slider {
                        id: brightnessSlider
                        objectName: "appBrightnessSlider"
                        Layout.fillWidth: true
                        implicitHeight: 38
                        from: 25; to: 100; stepSize: 1
                        value: workspace.shell ? workspace.shell.previewBrightness : 100
                        onMoved: workspace.shell.setPreference("preview_brightness", Math.round(value))
                        background: Rectangle { x: brightnessSlider.leftPadding; y: brightnessSlider.topPadding + brightnessSlider.availableHeight/2 - height/2; width: brightnessSlider.availableWidth; height: 5; radius: 3; color: workspace.colors.layer; Rectangle { width: brightnessSlider.visualPosition * parent.width; height: parent.height; radius: 3; color: workspace.colors.accent } }
                        handle: Rectangle { x: brightnessSlider.leftPadding + brightnessSlider.visualPosition * (brightnessSlider.availableWidth-width); y: brightnessSlider.topPadding + brightnessSlider.availableHeight/2-height/2; width: 24; height: 24; radius: 12; color: workspace.colors.surface; border.color: workspace.colors.accent; border.width: 2 }
                    }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.shell && workspace.shell.preferencesPersistent ? qsTr("Interface preferences are stored locally.") : qsTr("Interface preferences are in memory; persistent storage is unavailable.") }
                    Button {
        id: workspaceControl40_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl40_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl40_action.enabled ? workspace.colors.raised : workspaceControl40_action.emphasized ? workspace.colors.accent : workspaceControl40_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl40_action.activeFocus ? 2 : 1
            border.color: workspaceControl40_action.activeFocus || workspaceControl40_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl40_actionText
            text: workspaceControl40_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl40_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl40_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl40_action.enabled ? workspace.colors.helper : workspaceControl40_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appPreferencesResetButton"; text: qsTr("Reset interface preferences"); onClicked: workspace.shell.resetPreferences() }
                    Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.shell && workspace.shell.preferenceBackupName.length > 0 ? qsTr("Previous preferences preserved as ") + workspace.shell.preferenceBackupName : ""; visible: text.length > 0 }
                }
            }
        }
    }
    Component {
        id: storageSettings
        ColumnLayout {
            spacing: 14
            Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Local storage") } ColumnLayout {
        property var rows
        spacing: 0
        Repeater {
            model: parent.rows
            delegate: ColumnLayout {

                spacing: 6
                RowLayout {

                    Layout.topMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 16
                    Text { font.family: workspace.uiFont;  Layout.preferredWidth: Math.min(180, workspace.width * 0.25); Layout.alignment: Qt.AlignTop; text: qsTr(modelData.label); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.value; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: workspace.colors.line; opacity: 1 }
            }
        }
    
 Layout.fillWidth: true; rows: workspace.apps ? workspace.apps.storageRows : [] } Button {
        id: workspaceControl45_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl45_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl45_action.enabled ? workspace.colors.raised : workspaceControl45_action.emphasized ? workspace.colors.accent : workspaceControl45_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl45_action.activeFocus ? 2 : 1
            border.color: workspaceControl45_action.activeFocus || workspaceControl45_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl45_actionText
            text: workspaceControl45_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl45_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl45_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl45_action.enabled ? workspace.colors.helper : workspaceControl45_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 text: qsTr("Open retained photographs"); onClicked: workspace.shell.navigate("photos") } } }
        }
    }
    Component {
        id: systemSettings
        ColumnLayout {
            spacing: 14
            Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("System information") } ColumnLayout {
        property var rows
        spacing: 0
        Repeater {
            model: parent.rows
            delegate: ColumnLayout {

                spacing: 6
                RowLayout {

                    Layout.topMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 16
                    Text { font.family: workspace.uiFont;  Layout.preferredWidth: Math.min(180, workspace.width * 0.25); Layout.alignment: Qt.AlignTop; text: qsTr(modelData.label); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.value; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: workspace.colors.line; opacity: 1 }
            }
        }
    
 Layout.fillWidth: true; rows: workspace.apps ? workspace.apps.systemRows : [] } Button {
        id: workspaceControl49_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl49_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl49_action.enabled ? workspace.colors.raised : workspaceControl49_action.emphasized ? workspace.colors.accent : workspaceControl49_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl49_action.activeFocus ? 2 : 1
            border.color: workspaceControl49_action.activeFocus || workspaceControl49_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl49_actionText
            text: workspaceControl49_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl49_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl49_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl49_action.enabled ? workspace.colors.helper : workspaceControl49_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 text: qsTr("Open diagnostics"); onClicked: workspace.shell.navigate("diagnostics") } } }
        }
    }

    Component {
        id: legacyRecipesPage
        ScrollView {
            id: recipesScroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: recipesScroll.availableWidth
                spacing: 14
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: 12
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Create a bracket recipe") }
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Save a portable offline plan. Editing or saving does not take photographs; execution uses the separate workflow CLI.") }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: workspace.width >= 900 ? 2 : 1
                            columnSpacing: 12
                            rowSpacing: 10
                            TextField {
        id: recipeName
        implicitHeight: 44
        color: workspace.colors.text
        placeholderTextColor: workspace.colors.helper
        font.pixelSize: 13
        selectByMouse: true
        leftPadding: 12
        rightPadding: 12
        background: Rectangle {
            radius: 12
            color: workspace.colors.raised
            border.width: recipeName.activeFocus ? 2 : 1
            border.color: recipeName.activeFocus ? workspace.colors.accent : workspace.colors.line
        }
    
 objectName: "appRecipeNameInput"; Layout.fillWidth: true; text: "Three-exposure bracket"; placeholderText: qsTr("Recipe name"); maximumLength: 160 }
                            TextField {
        id: recipeExposures
        implicitHeight: 44
        color: workspace.colors.text
        placeholderTextColor: workspace.colors.helper
        font.pixelSize: 13
        selectByMouse: true
        leftPadding: 12
        rightPadding: 12
        background: Rectangle {
            radius: 12
            color: workspace.colors.raised
            border.width: recipeExposures.activeFocus ? 2 : 1
            border.color: recipeExposures.activeFocus ? workspace.colors.accent : workspace.colors.line
        }
    
 objectName: "appRecipeExposureInput"; Layout.fillWidth: true; text: "5000,10000,20000"; placeholderText: qsTr("Comma-separated exposures in us"); maximumLength: 4096 }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Uses the current mode and requested gain. Each listed exposure remains explicit.") }
                            Button {
        id: workspaceControl56_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl56_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl56_action.enabled ? workspace.colors.raised : workspaceControl56_action.emphasized ? workspace.colors.accent : workspaceControl56_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl56_action.activeFocus ? 2 : 1
            border.color: workspaceControl56_action.activeFocus || workspaceControl56_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl56_actionText
            text: workspaceControl56_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl56_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl56_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl56_action.enabled ? workspace.colors.helper : workspaceControl56_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appRecipeSaveButton"; text: qsTr("Save recipe"); emphasized: true; enabled: workspace.apps && recipeName.text.trim().length > 0 && recipeExposures.text.length > 0; onClicked: workspace.apps.saveBracketRecipe(recipeName.text.trim(), recipeExposures.text) }
                        }
                    }
                }
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: 12
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Saved portable plans") }
                        ListView {
                            id: recipeList
                            objectName: "appRecipeList"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.max(64, Math.min(210, count * 66))
                            clip: true
                            spacing: 6
                            model: workspace.apps ? workspace.apps.recipes : []
                            delegate: Button {
                                id: recipeButton
                                objectName: "appRecipeItem-" + modelData.id
                                width: recipeList.width
                                height: 60
                                onClicked: workspace.apps.selectRecipe(modelData.id)
                                property bool selected: workspace.apps.selectedRecipeId === modelData.id
                                background: Rectangle { radius: 12; color: recipeButton.selected ? workspace.colors.softAccent : workspace.colors.raised; border.color: recipeButton.selected ? workspace.colors.accent : workspace.colors.line }
                                contentItem: RowLayout { spacing: 12; Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.name; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 14; elide: Text.ElideRight } Text { font.family: workspace.uiFont;  text: modelData.exposureCount + qsTr(" frames / ") + modelData.sensorMode; color: workspace.colors.helper; font.pixelSize: 11 } }
                            }
                            Text { font.family: workspace.uiFont;  anchors.centerIn: parent; width: parent.width-20; visible: recipeList.count === 0; text: qsTr("No saved recipes. Create a bracket above."); color: workspace.colors.helper; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                        }
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.apps ? workspace.appNoticeMatches ? qsTr(workspace.apps.message) : "" : qsTr("Local recipe storage is unavailable."); visible: text.length > 0 }
                        TextArea {
                            objectName: "appRecipeSchemaText"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 230
                            text: workspace.apps ? workspace.apps.selectedRecipeJson : ""
                            placeholderText: qsTr("Select a saved recipe to inspect its portable schema.")
                            placeholderTextColor: workspace.colors.helper
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.WrapAnywhere
                            font.family: workspace.monoFont
                            font.pixelSize: 11
                            color: workspace.colors.text
                            padding: 12
                            background: Rectangle { color: workspace.colors.raised; radius: 12; border.color: workspace.colors.line }
                        }
                        TextField {
        id: recipeDestination
        implicitHeight: 44
        color: workspace.colors.text
        placeholderTextColor: workspace.colors.helper
        font.pixelSize: 13
        selectByMouse: true
        leftPadding: 12
        rightPadding: 12
        background: Rectangle {
            radius: 12
            color: workspace.colors.raised
            border.width: recipeDestination.activeFocus ? 2 : 1
            border.color: recipeDestination.activeFocus ? workspace.colors.accent : workspace.colors.line
        }
    
 objectName: "appRecipeExportPathInput"; Layout.fillWidth: true; placeholderText: qsTr("New absolute .json path, or leave blank for local exports") }
                        Button {
        id: workspaceControl61_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl61_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl61_action.enabled ? workspace.colors.raised : workspaceControl61_action.emphasized ? workspace.colors.accent : workspaceControl61_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl61_action.activeFocus ? 2 : 1
            border.color: workspaceControl61_action.activeFocus || workspaceControl61_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl61_actionText
            text: workspaceControl61_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl61_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl61_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl61_action.enabled ? workspace.colors.helper : workspaceControl61_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appRecipeExportButton"; text: qsTr("Export selected recipe"); enabled: workspace.apps && workspace.apps.selectedRecipeId.length > 0; onClicked: { if (recipeDestination.text.trim().length === 0) recipeDestination.text = workspace.apps.exportDirectory + "/recipe-" + Date.now() + ".json"; workspace.apps.exportRecipe(recipeDestination.text) } }
                    }
                }
            }
        }
    }
    Component {
        id: devicesPage
        ScrollView {
            id: devicesScroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: devicesScroll.availableWidth
                spacing: 14
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Connected camera source") } ColumnLayout {
        property var rows
        spacing: 0
        Repeater {
            model: parent.rows
            delegate: ColumnLayout {

                spacing: 6
                RowLayout {

                    Layout.topMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 16
                    Text { font.family: workspace.uiFont;  Layout.preferredWidth: Math.min(180, workspace.width * 0.25); Layout.alignment: Qt.AlignTop; text: qsTr(modelData.label); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.value; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: workspace.colors.line; opacity: 1 }
            }
        }
    
 Layout.fillWidth: true; rows: workspace.apps ? workspace.apps.deviceRows : [] } } }
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Hardware boundary") } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("HF-Sim is a software image source. No physical IMX410 sensor, FPGA receiver, HUMI lens interface or supervisor MCU is connected or validated.") } Button {
        id: workspaceControl68_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl68_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl68_action.enabled ? workspace.colors.raised : workspaceControl68_action.emphasized ? workspace.colors.accent : workspaceControl68_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl68_action.activeFocus ? 2 : 1
            border.color: workspaceControl68_action.activeFocus || workspaceControl68_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl68_actionText
            text: workspaceControl68_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl68_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl68_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl68_action.enabled ? workspace.colors.helper : workspaceControl68_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 text: qsTr("View capture diagnostics"); onClicked: workspace.shell.navigate("diagnostics") } } }
            }
        }
    }
    Component {
        id: diagnosticsPage
        ScrollView {
            id: diagnosticsScroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: diagnosticsScroll.availableWidth
                spacing: 14
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Capture health") } ColumnLayout {
        property var rows
        spacing: 0
        Repeater {
            model: parent.rows
            delegate: ColumnLayout {

                spacing: 6
                RowLayout {

                    Layout.topMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 16
                    Text { font.family: workspace.uiFont;  Layout.preferredWidth: Math.min(180, workspace.width * 0.25); Layout.alignment: Qt.AlignTop; text: qsTr(modelData.label); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.value; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: workspace.colors.line; opacity: 1 }
            }
        }
    
 Layout.fillWidth: true; rows: workspace.apps ? workspace.apps.diagnosticRows : [] } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Requested UI settings are shown here. Actual per-frame settings and completion evidence remain attached to retained captures.") } } }
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    

                    Layout.fillWidth: true
                    contentItem: ColumnLayout {
                        spacing: 12
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Export local diagnostics") }
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("The report contains current request state, runtime identity and filesystem capacity. Device temperatures and power are not measured.") }
                        TextField {
        id: diagnosticDestination
        implicitHeight: 44
        color: workspace.colors.text
        placeholderTextColor: workspace.colors.helper
        font.pixelSize: 13
        selectByMouse: true
        leftPadding: 12
        rightPadding: 12
        background: Rectangle {
            radius: 12
            color: workspace.colors.raised
            border.width: diagnosticDestination.activeFocus ? 2 : 1
            border.color: diagnosticDestination.activeFocus ? workspace.colors.accent : workspace.colors.line
        }
    
 objectName: "appDiagnosticExportPathInput"; Layout.fillWidth: true; placeholderText: qsTr("New absolute .json path, or leave blank for local exports") }
                        Button {
        id: workspaceControl77_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl77_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl77_action.enabled ? workspace.colors.raised : workspaceControl77_action.emphasized ? workspace.colors.accent : workspaceControl77_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl77_action.activeFocus ? 2 : 1
            border.color: workspaceControl77_action.activeFocus || workspaceControl77_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl77_actionText
            text: workspaceControl77_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl77_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl77_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl77_action.enabled ? workspace.colors.helper : workspaceControl77_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appDiagnosticExportButton"; text: qsTr("Export diagnostics"); emphasized: true; enabled: workspace.apps; onClicked: { if (diagnosticDestination.text.trim().length === 0) diagnosticDestination.text = workspace.apps.exportDirectory + "/diagnostics-" + Date.now() + ".json"; workspace.apps.exportDiagnostics(diagnosticDestination.text) } }
                        Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: workspace.appNoticeMatches ? qsTr(workspace.apps.message) : ""; visible: text.length > 0 }
                    }
                }
            }
        }
    }
    Component {
        id: systemPage
        ScrollView {
            id: systemScroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: systemScroll.availableWidth
                spacing: 14
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("DaOS Camera / FF-1") } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Runtime information is read from this process. Host UI previews are identified as their actual operating system; they are not labeled as board validation.") } ColumnLayout {
        property var rows
        spacing: 0
        Repeater {
            model: parent.rows
            delegate: ColumnLayout {

                spacing: 6
                RowLayout {

                    Layout.topMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 16
                    Text { font.family: workspace.uiFont;  Layout.preferredWidth: Math.min(180, workspace.width * 0.25); Layout.alignment: Qt.AlignTop; text: qsTr(modelData.label); textFormat: Text.PlainText; color: workspace.colors.helper; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    Text { font.family: workspace.uiFont;  Layout.fillWidth: true; text: modelData.value; textFormat: Text.PlainText; color: workspace.colors.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: workspace.colors.line; opacity: 1 }
            }
        }
    
 Layout.fillWidth: true; rows: workspace.apps ? workspace.apps.systemRows : [] } } }
                Frame {
        padding: 18
        background: Rectangle { color: workspace.colors.surface; radius: 20; border.color: workspace.colors.line }
    
 Layout.fillWidth: true; contentItem: ColumnLayout { spacing: 12; Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.text
        font.pixelSize: 18
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Interface preferences") } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("Reset only the local theme, framing grid and simulated preview brightness. Photographs, exposure settings and original capture IDs remain separate.") } Button {
        id: workspaceControl86_action
        property bool emphasized
        implicitHeight: 42
        implicitWidth: Math.max(92, workspaceControl86_actionText.implicitWidth + 28)
        padding: 12
        opacity: 1
        font.pixelSize: 13
        background: Rectangle {
            radius: 12
            color: !workspaceControl86_action.enabled ? workspace.colors.raised : workspaceControl86_action.emphasized ? workspace.colors.accent : workspaceControl86_action.down ? workspace.colors.layer : workspace.colors.raised
            border.width: workspaceControl86_action.activeFocus ? 2 : 1
            border.color: workspaceControl86_action.activeFocus || workspaceControl86_action.emphasized ? workspace.colors.accent : workspace.colors.line
        }
        contentItem: Text {
            id: workspaceControl86_actionText
            text: workspaceControl86_action.text
            textFormat: Text.PlainText
            font.pixelSize: workspaceControl86_action.font.pixelSize
            font.family: workspace.uiFont
            font.weight: workspaceControl86_action.emphasized ? Font.DemiBold : Font.Medium
            color: !workspaceControl86_action.enabled ? workspace.colors.helper : workspaceControl86_action.emphasized ? "#ffffff" : workspace.colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    
 objectName: "appSystemResetPreferencesButton"; text: qsTr("Reset interface preferences"); onClicked: workspace.shell.resetPreferences() } Text { font.family: workspace.uiFont; 
        textFormat: Text.PlainText
        color: workspace.colors.helper
        font.pixelSize: 12
        lineHeight: 1.22
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    
 text: qsTr("BSD A/B updates, real hardware recovery and manufacturing validation are unavailable in this prototype.") } } }
            }
        }
    }
}
