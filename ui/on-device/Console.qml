import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

MonochromeWindow {
    id: window
    objectName: "cameraWindow"
    visible: true
    width: 1280
    height: 800
    minimumWidth: 800
    minimumHeight: 600
    title: qsTranslate("Console", "DaOS Camera · HassleFree FF-1")
    color: window.theme.background
    font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
    font.pixelSize: 14
    BootSplash { anchors.fill:parent;z:20000;visible:typeof bootStatus !== "undefined" && !bootStatus.ready }
    SoftKeyboard {
        parent: Overlay.overlay
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
    }

    readonly property var theme: cameraShell.theme === "dark" ? retailTheme.dark : retailTheme.light
    readonly property string activeApp: cameraShell.currentApp
    readonly property bool expandedRail: width >= 1100 && cameraShell.menuOpen
    readonly property int railWidth: expandedRail ? 188 : 72
    Shortcut { sequence: "Ctrl+K"; onActivated: commandDialog.open() }
    Shortcut { sequence: "Meta+K"; onActivated: commandDialog.open() }

    readonly property string monoFont: Qt.platform.os === "windows" ? "Consolas" : "monospace"
    readonly property color ink: theme.text
    readonly property color muted: theme.muted
    readonly property color line: theme.line
    readonly property color accent: theme.accent
    readonly property color accentText: "#ffffff"
    readonly property color warning: cameraShell.theme === "dark" ? "#ffffff" : "#000000"
    readonly property string actualRuntimeLabel: typeof runtimeOSLabel !== "undefined" ? runtimeOSLabel : "HF-SIM / ON-DEVICE"
    readonly property bool captureRunning: camera.captureState === "submitting" || camera.captureState === "accepted" || camera.captureState === "capturing"
    readonly property bool uncertain: camera.captureState === "unknown" || camera.connectionState === "unknown"
    property bool recordDetailsExpanded: false
    readonly property bool recordDetailsVisible: recordDetailsExpanded || uncertain || camera.captureState === "acknowledged"
    readonly property bool settingsEnabled: camera.connectionState === "ready" && !camera.busy && !captureRunning && !uncertain
    readonly property color stateColor: uncertain ? warning : camera.connectionState === "ready" ? window.theme.success : camera.connectionState === "disconnected" ? window.theme.error : muted

    palette.window: window.theme.background
    palette.windowText: ink
    palette.base: window.theme.raised
    palette.text: ink
    palette.button: window.theme.layer
    palette.buttonText: ink
    palette.highlight: accent
    palette.highlightedText: accentText
    palette.mid: line
    palette.dark: window.theme.muted

    onClosing: camera.shutdown()

    function exposureLabel(value) {
        if (value >= 1000000)
            return (value / 1000000).toFixed(2) + " s"
        if (value >= 1000)
            return (value / 1000).toFixed(1) + " ms"
        return value + " µs"
    }

    function openMenuCommand(command) {
        if (command.targetGroup)
            cameraShell.selectGroup(command.targetGroup)
        cameraShell.navigate(command.targetApp || command.id)
        commandDialog.close()
    }

    component ActionButton: Button {
        id: action
        property bool primary: false
        property bool quiet: false
        implicitHeight: primary ? 58 : 42
        implicitWidth: 110
        font.pixelSize: primary ? 15 : 13
        font.weight: primary ? Font.DemiBold : Font.Medium
        leftPadding: 14
        rightPadding: 14
        Accessible.name: text
        contentItem: Text {
            text: action.text
            font: action.font
            color: !action.enabled ? window.theme.helper : action.primary ? window.accentText : window.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 12
            color: !action.enabled ? window.theme.layer : action.primary ? (action.down ? window.theme.accentHover : action.hovered ? window.theme.accentHover : window.accent) : action.down ? window.theme.layer : action.hovered ? window.theme.raised : window.theme.surface
            border.width: action.activeFocus ? 2 : 1
            border.color: action.activeFocus ? window.accent : action.primary && action.enabled ? window.accent : window.line
        }
    }

    component FieldLabel: Label {
        color: window.muted
        font.pixelSize: 12
        font.weight: Font.Medium
    }

    component NumberInput: SpinBox {
        id: number
        implicitHeight: 46
        editable: true
        font.pixelSize: 17
        palette.text: window.ink
        palette.base: window.theme.raised
        background: Rectangle {
            color: number.enabled ? window.theme.raised : window.theme.layer
            radius: 12
            border.color: number.activeFocus ? window.accent : window.line
            border.width: number.activeFocus ? 2 : 1
        }
        contentItem: TextInput {
            z: 2
            text: number.displayText
            font: number.font
            color: number.enabled ? window.ink : window.theme.helper
            selectionColor: window.accent
            selectedTextColor: window.accentText
            horizontalAlignment: Qt.AlignHCenter
            verticalAlignment: Qt.AlignVCenter
            readOnly: !number.editable
            validator: number.validator
            inputMethodHints: number.inputMethodHints
            selectByMouse: true
            leftPadding: 2
            rightPadding: 2
        }
        up.indicator: Rectangle {
            x: number.mirrored ? 0 : number.width - width
            y: 0
            width: 39
            height: number.height
            radius: 12
            color: number.up.pressed ? window.theme.softAccent : window.theme.layer
            Text {
            font.family: window.font.family

                anchors.centerIn: parent
                text: "+"
                color: number.enabled && number.up.enabled ? window.ink : window.theme.helper
                font.pixelSize: 22
            }
        }
        down.indicator: Rectangle {
            x: number.mirrored ? number.width - width : 0
            y: 0
            width: 39
            height: number.height
            radius: 12
            color: number.down.pressed ? window.theme.softAccent : window.theme.layer
            Text {
            font.family: window.font.family

                anchors.centerIn: parent
                text: "−"
                color: number.enabled && number.down.enabled ? window.ink : window.theme.helper
                font.pixelSize: 22
            }
        }
    }

    Rectangle { anchors.fill: parent; color: window.color; z: -1 }

    ColumnLayout {
        id: cameraWorkspace
        visible: window.activeApp === "camera"
        anchors.fill: parent
        anchors.margins: 16
        anchors.leftMargin: window.railWidth + 32
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            spacing: 13
            Rectangle {
                visible: window.width >= 1100
                Layout.preferredWidth: 44
                Layout.preferredHeight: 44
                radius: 11
                color: window.accent
                Text {
            font.family: window.font.family

                    anchors.centerIn: parent
                    text: qsTranslate("Console", "HF")
                    color: "#ffffff"
                    font.pixelSize: 20
                    font.weight: Font.Bold
                    font.letterSpacing: -1
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Label {
                    text: window.width < 1000 ? qsTranslate("Console", "DaOS Camera") : qsTranslate("Console", "DaOS Camera / FF-1")
                    color: window.ink
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.2
                }
                Label {
                    Layout.fillWidth: true
                    text: camera.backendLabel
                    color: window.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                    Accessible.name: qsTranslate("Console", "Camera backend: ") + camera.backendLabel
                }
            }
            Rectangle {
                Layout.preferredHeight: 32
                Layout.preferredWidth: connectionLabel.implicitWidth + 38
                radius: 16
                color: window.theme.surface
                border.color: window.theme.line
                Row {
                    anchors.centerIn: parent
                    spacing: 7
                    Rectangle { width: 6; height: 6; radius: 3; color: window.stateColor; anchors.verticalCenter: parent.verticalCenter }
                    Label {
                        id: connectionLabel
                        text: window.uncertain ? qsTranslate("Console", "UNKNOWN") : camera.connectionState.toUpperCase()
                        color: window.stateColor
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: 0.7
                    }
                }
            }
            ActionButton {
                objectName: "libraryButton"
                text: qsTranslate("Console", "Retained photos")
                implicitWidth: 142
                enabled: camera.canBrowseCaptures && !camera.busy && !camera.libraryLoading
                onClicked: {
                    libraryDialog.open()
                    camera.refreshLibrary()
                }
                Accessible.description: qsTranslate("Console", "Browse completed retained captures without changing the current request.")
            }
            ActionButton {
                objectName: "reconnectButton"
                text: qsTranslate("Console", "Reconnect")
                enabled: !camera.busy && !window.captureRunning
                onClicked: camera.reconnect()
                Accessible.description: qsTranslate("Console", "Reconnect explicitly, without submitting another capture.")
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(66, errorLabel.implicitHeight + 20)
            visible: camera.errorText.length > 0
            color: window.theme.softAccent
            border.color: window.theme.line
            radius: 12
            clip: true
            Label {
                id: errorLabel
                anchors.fill: parent
                anchors.margins: 10
                text: qsTranslate("Console", camera.errorText)
                color: window.warning
                font.pixelSize: 12
                wrapMode: Text.Wrap
                Accessible.name: qsTranslate("Console", "Camera error: ") + qsTranslate("Console", camera.errorText)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 170
                    color: "#000000"
                    radius: 12
                    border.color: window.theme.line
                    clip: true
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTranslate("Console", "VIEWFINDER")
                                color: "#cccccc"
                                font.pixelSize: 10
                                font.weight: Font.DemiBold
                                font.letterSpacing: 1.5
                            }
                            Rectangle {
                                implicitWidth: 78
                                implicitHeight: 24
                                radius: 4
                                color: "#222222"
                                border.color: "#444444"
                                Label {
                                    anchors.centerIn: parent
                                    text: qsTranslate("Console", "HF-SIM")
                                    color: "#dddddd"
                                    font.pixelSize: 10
                                    font.weight: Font.DemiBold
                                    font.letterSpacing: 1
                                }
                            }
                            ToolButton {
                                id: gridToggle
                                text: checked ? qsTranslate("Console", "Grid on") : qsTranslate("Console", "Grid off")
                                checkable: true
                                checked: cameraShell.gridEnabled
                                onToggled: cameraShell.setPreference("grid_enabled", checked)
                                implicitHeight: 34
                                implicitWidth: 66
                                font.pixelSize: 11
                                palette.buttonText: "#cccccc"
                                Accessible.name: qsTranslate("Console", "Rule of thirds overlay")
                            }
                        }
                        Item {
                            id: previewArea
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            Image {
                                id: previewImage
                                anchors.fill: parent
                                source: camera.previewUrl
                                opacity: cameraShell.previewBrightness / 100.0
                                visible: camera.previewActive && source.toString().length > 0 && status !== Image.Error
                                fillMode: Image.PreserveAspectFit
                                smooth: true
                                cache: false
                                Accessible.name: qsTranslate("Console", "Independent synthetic HF-Sim preview; this is not a sensor image.")
                            }
                            Item {
                                anchors.centerIn: parent
                                width: previewImage.visible && previewImage.paintedWidth > 0 ? previewImage.paintedWidth : parent.width
                                height: previewImage.visible && previewImage.paintedHeight > 0 ? previewImage.paintedHeight : parent.height
                                visible: gridToggle.checked
                                Repeater {
                                    model: 2
                                    Rectangle { x: (index + 1) * parent.width / 3; width: 1; height: parent.height; color: "#ffffff"; opacity: 0.25 }
                                }
                                Repeater {
                                    model: 2
                                    Rectangle { y: (index + 1) * parent.height / 3; width: parent.width; height: 1; color: "#ffffff"; opacity: 0.25 }
                                }
                            }
                            Column {
                                anchors.centerIn: parent
                                spacing: 11
                                visible: !previewImage.visible
                                Rectangle {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    width: 42; height: 32; radius: 6
                                    color: "#000000"
                                    border.color: "#666666"
                                    Rectangle { anchors.centerIn: parent; width: 12; height: 12; radius: 6; color: "#000000"; border.color: "#aaaaaa" }
                                }
                                Label {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: camera.previewActive ? qsTranslate("Console", "Waiting for a synthetic frame") : qsTranslate("Console", "Preview is stopped")
                                    color: "#cccccc"
                                    font.pixelSize: 15
                                }
                                Label {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: qsTranslate("Console", "Start preview to compose the synthetic scene.")
                                    color: "#888888"
                                    font.pixelSize: 11
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTranslate("Console", "MANUAL  ·  ") + window.exposureLabel(camera.exposureUs) + "  ·  " + camera.analogueGain.toFixed(2) + "×"
                                color: "#aaaaaa"
                                font.pixelSize: 11
                            }
                            ActionButton {
                                objectName: "previewButton"
                                text: camera.previewActive ? qsTranslate("Console", "Stop preview") : qsTranslate("Console", "Start preview")
                                implicitHeight: 38
                                implicitWidth: 122
                                enabled: camera.connectionState === "ready" && !camera.busy && !window.captureRunning && !window.uncertain
                                onClicked: camera.togglePreview()
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTranslate("Console", "Synthetic preview · no sensor connected · separate from saved photographs")
                            color: "#888888"
                            font.pixelSize: 10
                            wrapMode: Text.Wrap
                        }
                    }
                }

                Rectangle {
                    objectName: "captureDetailsPanel"
                    Layout.fillWidth: true
                    Layout.preferredHeight: window.recordDetailsVisible ? 174 : 86
                    Layout.minimumHeight: window.recordDetailsVisible ? 158 : 80
                    color: window.theme.surface
                    radius: 10
                    border.color: window.theme.line
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 7
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: qsTranslate("Console", "LAST PHOTOGRAPH"); color: window.muted; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1 }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: camera.captureState === "acknowledged" ? qsTranslate("Console", "UNKNOWN / ACKNOWLEDGED") : camera.captureState.toUpperCase()
                                color: window.uncertain || camera.captureState === "acknowledged" ? window.warning : camera.captureState === "complete" ? window.theme.success : window.ink
                                font.pixelSize: 10
                                font.weight: Font.DemiBold
                                Accessible.name: qsTranslate("Console", "Capture state: ") + camera.captureState
                            }
                            ActionButton {
                                objectName: "captureDetailsButton"
                                text: window.recordDetailsVisible ? qsTranslate("Console", "Hide details") : qsTranslate("Console", "Details")
                                quiet: true
                                implicitWidth: 88
                                implicitHeight: 28
                                enabled: !window.uncertain && camera.captureState !== "acknowledged"
                                onClicked: window.recordDetailsExpanded = !window.recordDetailsExpanded
                                Accessible.description: qsTranslate("Console", "Show the original request ID and confirmed storage path. Unknown outcomes always keep their details visible.")
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: camera.statusText
                            color: window.muted
                            font.pixelSize: 12
                            elide: Text.ElideRight
                            Accessible.name: camera.statusText
                        }
                        TextEdit {
                            visible: window.recordDetailsVisible
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.max(17, contentHeight)
                            text: camera.lastRequestId.length ? camera.lastRequestId : (window.uncertain || camera.captureState === "acknowledged" ? "Previous request ID is unavailable" : "No capture request yet")
                            color: camera.lastRequestId.length ? window.ink : window.muted
                            font.family: window.monoFont
                            font.pixelSize: 12
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.WrapAnywhere
                            Accessible.name: qsTranslate("Console", "Original request ID: ") + text
                        }
                        Rectangle { visible: window.recordDetailsVisible; Layout.fillWidth: true; Layout.preferredHeight: 1; color: window.line }
                        ScrollView {
                            visible: window.recordDetailsVisible
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                            TextArea {
                                text: camera.dngPath.length ? camera.dngPath : "DNG path appears after confirmed completion."
                                color: window.muted
                                font.family: window.monoFont
                                font.pixelSize: 11
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.WrapAnywhere
                                leftPadding: 0; rightPadding: 6; topPadding: 0; bottomPadding: 0
                                background: null
                                Accessible.name: qsTranslate("Console", "Committed DNG path: ") + text
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.preferredWidth: window.width < 900 ? 270 : 308
                Layout.fillHeight: true
                color: window.theme.surface
                radius: 12
                border.color: window.theme.line
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: qsTranslate("Console", "Manual exposure"); color: window.ink; font.pixelSize: 20; font.weight: Font.Medium }
                        Item { Layout.fillWidth: true }
                        Label { text: qsTranslate("Console", "DNG"); color: window.accent; font.pixelSize: 11; font.weight: Font.DemiBold }
                    }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        contentWidth: availableWidth
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ColumnLayout {
                            width: parent.width
                            spacing: 14
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                FieldLabel { text: qsTranslate("Console", "Capture size") }
                                LocalizedComboBox {
                                    id: modeCombo
                                    objectName: "modeCombo"
                                    Layout.fillWidth: true
                                    implicitHeight: 44
                                    enabled: window.settingsEnabled
                                    model: ["sim-small", "sim-full"]
                                    currentIndex: camera.sensorMode === "sim-full" ? 1 : 0
                                    onActivated: camera.setSensorMode(currentText)
                                    Accessible.name: qsTranslate("Console", "Simulator sensor mode")
                                    background: Rectangle { color: window.theme.raised; radius: 12; border.color: modeCombo.activeFocus ? window.accent : window.line }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                RowLayout {
                                    Layout.fillWidth: true
                                    FieldLabel { text: qsTranslate("Console", "Requested exposure"); Layout.fillWidth: true }
                                    FieldLabel { text: "µs" }
                                }
                                NumberInput {
                                    objectName: "exposureInput"
                                    Layout.fillWidth: true
                                    from: 100
                                    to: 30000000
                                    stepSize: 100
                                    value: camera.exposureUs
                                    enabled: window.settingsEnabled
                                    inputMethodHints: Qt.ImhDigitsOnly
                                    onValueModified: camera.setExposureUs(value)
                                    Accessible.name: qsTranslate("Console", "Requested exposure in microseconds")
                                    Accessible.description: qsTranslate("Console", "From 100 through 30000000 microseconds.")
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                FieldLabel { text: qsTranslate("Console", "Requested analogue gain") }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 9
                                    Slider {
                                        id: gainSlider
                                        objectName: "gainSlider"
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 72
                                        implicitHeight: 44
                                        from: 1.0
                                        to: 16.0
                                        stepSize: 0.1
                                        value: camera.analogueGain
                                        enabled: window.settingsEnabled
                                        onMoved: camera.setAnalogueGain(value)
                                        Accessible.name: qsTranslate("Console", "Requested analogue gain")
                                        background: Rectangle {
                                            x: gainSlider.leftPadding
                                            y: gainSlider.topPadding + gainSlider.availableHeight / 2 - height / 2
                                            width: gainSlider.availableWidth
                                            height: 4
                                            radius: 2
                                            color: window.theme.line
                                            Rectangle { width: gainSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: window.accent }
                                        }
                                        handle: Rectangle {
                                            x: gainSlider.leftPadding + gainSlider.visualPosition * (gainSlider.availableWidth - width)
                                            y: gainSlider.topPadding + gainSlider.availableHeight / 2 - height / 2
                                            width: 24; height: 24; radius: 12
                                            color: gainSlider.enabled ? window.accent : window.theme.helper
                                            border.width: gainSlider.activeFocus ? 2 : 0
                                            border.color: "#ffffff"
                                        }
                                    }
                                    NumberInput {
                                        id: gainInput
                                        Layout.preferredWidth: 128
                                        from: 100
                                        to: 1600
                                        stepSize: 10
                                        value: Math.round(camera.analogueGain * 100)
                                        enabled: window.settingsEnabled
                                        font.pixelSize: 14
                                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                                        validator: DoubleValidator { bottom: 1.0; top: 16.0; decimals: 2; notation: DoubleValidator.StandardNotation; locale: gainInput.locale.name }
                                        textFromValue: function(value, locale) { return Number(value / 100).toLocaleString(locale, "f", 2) }
                                        valueFromText: function(text, locale) { return Math.round(Number.fromLocaleString(locale, text) * 100) }
                                        onValueModified: camera.setAnalogueGain(value / 100)
                                        Accessible.name: qsTranslate("Console", "Analogue gain multiplier")
                                    }
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: camera.sensorMode === "sim-full" ? qsTranslate("Console", "6064 × 4040 · 14-bit Bayer in 16-bit containers") : qsTranslate("Console", "128 × 96 · 14-bit Bayer in 16-bit containers")
                                color: window.theme.helper
                                font.pixelSize: 10
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: window.line }
                    Label {
                        Layout.fillWidth: true
                        Layout.minimumHeight: implicitHeight
                        visible: window.uncertain || camera.captureState === "acknowledged"
                        text: camera.captureState === "unknown"
                              ? qsTranslate("Console", "The prior request stays unknown. A NEW shot can create another image. Querying or restarting does not acknowledge it.")
                              : camera.captureState === "acknowledged"
                                ? qsTranslate("Console", "The prior request stays unknown. You allowed a NEW shot, which can create another image.")
                                : qsTranslate("Console", "Connection is unconfirmed. Reconnect and query the original request before another shot.")
                        color: window.warning
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    ActionButton {
                        id: acknowledgeButton
                        objectName: "acknowledgeUnknownButton"
                        Layout.fillWidth: true
                        Layout.minimumHeight: 56
                        implicitHeight: 56
                        visible: camera.captureState === "unknown"
                        enabled: camera.canAcknowledgeUnknown
                        text: qsTranslate("Console", "Keep record and allow a new shot")
                        onClicked: camera.acknowledgeUnknown()
                        contentItem: Text {
                            text: acknowledgeButton.text
                            font: acknowledgeButton.font
                            color: acknowledgeButton.enabled ? window.warning : window.theme.helper
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            wrapMode: Text.Wrap
                        }
                        background: Rectangle {
                            radius: 12
                            color: acknowledgeButton.down ? window.theme.softAccent : window.theme.raised
                            border.color: acknowledgeButton.activeFocus ? window.accent : acknowledgeButton.enabled ? window.theme.line : window.line
                            border.width: acknowledgeButton.activeFocus ? 2 : 1
                        }
                        Accessible.description: qsTranslate("Console", "Keep the original request unknown and explicitly permit a new request ID. A new shot can create another image. Querying and restarting do not acknowledge it.")
                    }
                    ActionButton {
                        objectName: "shutterButton"
                        Layout.fillWidth: true
                        Layout.minimumHeight: 58
                        primary: true
                        text: window.uncertain ? qsTranslate("Console", "OUTCOME UNKNOWN") : window.captureRunning ? qsTranslate("Console", "CAPTURE IN PROGRESS") : camera.captureState === "acknowledged" ? qsTranslate("Console", "CAPTURE NEW FRAME") : qsTranslate("Console", "CAPTURE FRAME")
                        enabled: camera.canCapture && !camera.busy && !window.captureRunning && !window.uncertain && camera.connectionState === "ready"
                        onClicked: camera.shutter()
                        Accessible.description: qsTranslate("Console", "Submit one capture. An uncertain request is never retried automatically.")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        ActionButton {
                            objectName: "cancelButton"
                            Layout.fillWidth: true
                            text: qsTranslate("Console", "Cancel capture")
                            enabled: window.captureRunning && camera.connectionState === "ready"
                            onClicked: camera.cancelCapture()
                            Accessible.description: qsTranslate("Console", "Request cancellation, then inspect the resulting status.")
                        }
                        ActionButton {
                            objectName: "queryButton"
                            Layout.fillWidth: true
                            text: qsTranslate("Console", "Query original")
                            enabled: camera.lastRequestId.length > 0 && !camera.busy
                            onClicked: camera.queryLast()
                            Accessible.description: qsTranslate("Console", "Query the saved original request ID without taking another frame.")
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Label { objectName: "runtimeOSFooter"; text: window.actualRuntimeLabel; color: window.theme.helper; font.pixelSize: 10; font.letterSpacing: 0.5 }
            Item { Layout.fillWidth: true }
            Label { text: window.width < 1000 ? qsTranslate("Console", "HF-Sim / RAW") : qsTranslate("Console", "Synthetic capture · no measured hardware calibration"); color: window.theme.helper; font.pixelSize: 10 }
        }
    }
    Dialog {
        id: libraryDialog
        objectName: "libraryDialog"
        parent: Overlay.overlay
        modal: true
        focus: true
        x: (window.width - width) / 2
        y: (window.height - height) / 2
        width: Math.min(window.width - 24, 1040)
        height: Math.min(window.height - 24, 680)
        padding: 12
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        property string trackedSelection: camera.selectedCaptureId
        onTrackedSelectionChanged: libraryExportPath.clear()
        onOpened: libraryExportPath.clear()
        background: Rectangle {
            color: window.theme.surface
            radius: 12
            border.color: window.theme.line
        }
        header: Rectangle {
            implicitHeight: 54
            color: window.theme.raised
            radius: 10
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 10
                spacing: 12
                Label {
                    Layout.fillWidth: true
                    text: qsTranslate("Console", "Retained photos")
                    textFormat: Text.PlainText
                    color: window.ink
                    font.pixelSize: 19
                    font.weight: Font.DemiBold
                }
                ActionButton {
                    text: qsTranslate("Console", "Close")
                    quiet: true
                    implicitWidth: 76
                    onClicked: libraryDialog.close()
                }
            }
        }
        contentItem: Item {
            implicitHeight: libraryContent.implicitHeight
            ColumnLayout {
            id: libraryContent
            objectName: "libraryContent"
            anchors.fill: parent
            spacing: 9
            RowLayout {
                Layout.fillWidth: true
                spacing: 9
                ActionButton {
                    objectName: "libraryRefreshButton"
                    text: qsTranslate("Console", "Newest / refresh")
                    implicitWidth: 142
                    enabled: camera.canBrowseCaptures && !camera.busy && !camera.libraryLoading
                    onClicked: camera.refreshLibrary()
                }
                ActionButton {
                    objectName: "libraryNextButton"
                    text: qsTranslate("Console", "Next older")
                    implicitWidth: 112
                    enabled: camera.canBrowseCaptures && !camera.busy && !camera.libraryLoading && camera.libraryNextCursor.length > 0
                    onClicked: camera.nextLibraryPage()
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: camera.libraryLoading ? qsTranslate("Console", "Loading...") : camera.libraryItems.length + qsTranslate("Console", " on this page")
                    textFormat: Text.PlainText
                    color: window.muted
                    font.pixelSize: 11
                }
            }
            Label {
                Layout.fillWidth: true
                text: camera.libraryMessage
                textFormat: Text.PlainText
                color: window.muted
                font.pixelSize: 11
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12
                Rectangle {
                    Layout.preferredWidth: Math.max(248, Math.floor(libraryDialog.availableWidth * 0.36))
                    Layout.fillHeight: true
                    radius: 12
                    color: window.theme.raised
                    border.color: window.theme.line
                    ListView {
                        id: libraryList
                        objectName: "libraryList"
                        anchors.fill: parent
                        anchors.margins: 3
                        model: camera.libraryItems
                        clip: true
                        spacing: 3
                        cacheBuffer: 0
                        currentIndex: -1
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        function chooseRow(index) {
                            if (index < 0 || index >= count || camera.libraryLoading || camera.busy || !camera.canBrowseCaptures)
                                return
                            currentIndex = index
                            camera.selectCapture(model[index].request_id)
                            positionViewAtIndex(index, ListView.Contain)
                        }
                        Keys.onUpPressed: chooseRow(Math.max(0, currentIndex - 1))
                        Keys.onDownPressed: chooseRow(Math.min(count - 1, currentIndex + 1))
                        delegate: ItemDelegate {
                            id: captureRow
                            objectName: "capture-" + modelData.request_id
                            width: libraryList.width
                            height: 82
                            enabled: camera.canBrowseCaptures && !camera.busy && !camera.libraryLoading
                            focusPolicy: Qt.StrongFocus
                            onClicked: {
                                libraryList.currentIndex = index
                                camera.selectCapture(modelData.request_id)
                            }
                            Accessible.name: qsTranslate("Console", "Retained capture ") + modelData.request_id
                            Accessible.description: qsTranslate("Console", "Select a completed DNG for read-only display conversion and export.")
                            background: Rectangle {
                                radius: 6
                                color: camera.selectedCaptureId === modelData.request_id ? window.theme.softAccent : captureRow.down ? window.theme.layer : captureRow.hovered ? window.theme.layer : window.theme.surface
                                border.color: captureRow.activeFocus ? window.accent : camera.selectedCaptureId === modelData.request_id ? window.theme.accent : window.theme.line
                                border.width: captureRow.activeFocus ? 2 : 1
                            }
                            contentItem: Column {
                                spacing: 5
                                Text {
                                    width: parent.width
                                    text: modelData.filename
                                    textFormat: Text.PlainText
                                    font.family: window.monoFont
                                    font.pixelSize: 11
                                    color: window.ink
                                    elide: Text.ElideMiddle
                                }
                                Text {
            font.family: window.font.family

                                    width: parent.width
                                    text: modelData.sensor_mode + "  |  " + modelData.width + " x " + modelData.height
                                    textFormat: Text.PlainText
                                    font.pixelSize: 11
                                    color: window.theme.muted
                                    elide: Text.ElideRight
                                }
                                Text {
            font.family: window.font.family

                                    width: parent.width
                                    text: window.exposureLabel(modelData.actual_exposure_us) + qsTranslate("Console", "  |  gain ") + Number(modelData.actual_analogue_gain).toFixed(2)
                                    textFormat: Text.PlainText
                                    font.pixelSize: 11
                                    color: window.theme.muted
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        width: parent.width - 24
                        text: camera.libraryLoading ? qsTranslate("Console", "Loading retained captures...") : qsTranslate("Console", "No retained captures on this page.")
                        textFormat: Text.PlainText
                        color: window.muted
                        font.pixelSize: 13
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        visible: libraryList.count === 0
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 8
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 92
                        color: "#000000"
                        radius: 12
                        border.color: window.theme.line
                        clip: true
                        Image {
                            objectName: "libraryReviewImage"
                            anchors.fill: parent
                            anchors.margins: 8
                            source: camera.reviewUrl
                            visible: camera.reviewUrl.length > 0
                            fillMode: Image.PreserveAspectFit
                            smooth: true
                            cache: false
                            Accessible.name: qsTranslate("Console", "Display conversion sampled from the selected retained DNG.")
                        }
                        Label {
                            anchors.centerIn: parent
                            width: parent.width - 24
                            text: camera.selectedCaptureId.length ? qsTranslate("Console", "Retained DNG display is unavailable.") : qsTranslate("Console", "Select a retained capture.")
                            textFormat: Text.PlainText
                            color: window.muted
                            font.pixelSize: 13
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            visible: camera.reviewUrl.length === 0
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTranslate("Console", "Retained DNG display conversion - synthetic capture, not live preview; uncalibrated color.")
                        textFormat: Text.PlainText
                        color: window.theme.helper
                        font.pixelSize: 10
                        wrapMode: Text.Wrap
                    }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 72
                        Layout.minimumHeight: 44
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        TextArea {
                            text: camera.selectedCaptureInfo.length ? camera.selectedCaptureInfo : "Selection is separate from the current capture request."
                            textFormat: TextEdit.PlainText
                            color: window.ink
                            font.family: window.monoFont
                            font.pixelSize: 11
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.WrapAnywhere
                            leftPadding: 0; rightPadding: 5; topPadding: 0; bottomPadding: 0
                            background: null
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTranslate("Console", "Export a copy - destination must be a new local .dng file")
                        textFormat: Text.PlainText
                        color: window.muted
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        TextField {
                            id: libraryExportPath
                            objectName: "libraryExportPath"
                            Layout.fillWidth: true
                            implicitHeight: 44
                            placeholderText: qsTranslate("Console", "Absolute destination path")
                            text: ""
                            color: window.ink
                            selectByMouse: true
                            enabled: camera.canBrowseCaptures && !camera.busy && !camera.libraryLoading && camera.selectedCaptureId.length > 0
                            font.pixelSize: 12
                            background: Rectangle {
                                color: window.theme.raised
                                radius: 6
                                border.color: libraryExportPath.activeFocus ? window.accent : "#444444"
                            }
                            Accessible.name: qsTranslate("Console", "New local DNG export destination")
                        }
                        ActionButton {
                            objectName: "libraryExportButton"
                            text: qsTranslate("Console", "Export DNG")
                            implicitHeight: 44
                            implicitWidth: 112
                            enabled: camera.canBrowseCaptures && !camera.busy && !camera.libraryLoading && camera.selectedCaptureId.length > 0 && libraryExportPath.text.trim().length > 0
                            onClicked: camera.exportSelected(libraryExportPath.text)
                            Accessible.description: qsTranslate("Console", "Create a new copy of the selected DNG. Existing files are never replaced.")
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: camera.exportMessage
                        textFormat: Text.PlainText
                        color: window.warning
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        visible: text.length > 0
                    }
                }
            }
            }
        }
    }


    DaOSDock {
        id: navigationDock
        objectName: "DaOSSidebar"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.margins: 12
        width: window.railWidth
        expanded: window.expandedRail
        expandAvailable: window.width >= 1100
        shell: cameraShell
        theme: window.theme
        onCommandRequested: commandDialog.open()
        onLauncherRequested: launcherDialog.open()
    }

    AppsWorkspace {
        objectName: "DaOSAppsWorkspace"
        anchors.fill: parent
        anchors.margins: 16
        anchors.leftMargin: window.railWidth + 32
        visible: window.activeApp !== "camera"
        shell: cameraShell
        cameraController: camera
        apps: cameraApps
        theme: window.theme
    }

    Dialog {
        id: commandDialog
        objectName: "commandDialog"
        parent: Overlay.overlay
        modal: true
        focus: true
        x: (window.width - width) / 2
        y: Math.min(100, (window.height-height)/2)
        width: Math.min(window.width - 48, 560)
        height: Math.min(window.height - 60, 500)
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        property var commands: {
            var q = commandSearch.text.toLowerCase().trim()
            var list = cameraShell.apps.filter(function(app) { return app.enabled })
            var settingsAvailable = list.some(function(app) { return app.id === "settings" })
            if (settingsAvailable) {
                cameraShell.settingsGroups.forEach(function(group) {
                    list.push({id: "settings-" + group.id, title: "Settings / " + group.title,
                               targetApp: "settings", targetGroup: group.id})
                })
            }
            return list.filter(function(command) {
                return command.title.toLowerCase().indexOf(q) !== -1 || command.id.indexOf(q) !== -1
            })
        }
        onOpened: { commandSearch.clear(); commandSearch.forceActiveFocus(); commandList.currentIndex=0 }
        background: Rectangle { radius: 24; color: window.theme.surface; border.color: window.line }
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Label { text: qsTranslate("Console", "Search DaOS"); color: window.ink; font.pixelSize: 18; font.weight: Font.DemiBold }
            TextField {
                id: commandSearch
                objectName: "commandSearchInput"
                Layout.fillWidth: true
                placeholderText: qsTranslate("Console", "Find an app or camera workspace...")
                selectByMouse: true
                onTextChanged: commandList.currentIndex=0
                Keys.onDownPressed: commandList.currentIndex=Math.min(commandList.count-1,commandList.currentIndex+1)
                Keys.onUpPressed: commandList.currentIndex=Math.max(0,commandList.currentIndex-1)
                onAccepted: {
                    if(commandList.currentIndex >= 0 && commandList.currentIndex < commandDialog.commands.length) {
                        window.openMenuCommand(commandDialog.commands[commandList.currentIndex])
                    }
                }
            }
            ListView {
                id: commandList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: commandDialog.commands
                currentIndex: 0
                spacing: 4
                delegate: ItemDelegate {
                    objectName: "command-result-" + modelData.id
                    width: commandList.width
                    height: 48
                    text: qsTranslate("Console", modelData.title)
                    highlighted: index===commandList.currentIndex
                    onClicked: window.openMenuCommand(modelData)
                    contentItem: Row {
                        spacing: 14
                        DaOSIcon { kind: modelData.targetApp || modelData.id; ink: index===commandList.currentIndex ? window.accent : window.muted; anchors.verticalCenter: parent.verticalCenter }
                        Label { text: qsTranslate("Console", modelData.title); color: window.ink; anchors.verticalCenter: parent.verticalCenter }
                    }
                    background: Rectangle { radius: 12; color: index===commandList.currentIndex ? window.theme.softAccent : window.theme.surface }
                }
            }
            Label { text: commandList.count ? qsTranslate("Console", "Navigate with arrows · Enter to open · Esc to close") : qsTranslate("Console", "No matching apps"); color: window.muted; font.pixelSize: 11 }
        }
    }

    Dialog {
        id: launcherDialog
        objectName: "launcherDialog"
        parent: Overlay.overlay
        modal: true
        focus: true
        x: (window.width-width)/2
        y: (window.height-height)/2
        width: Math.min(window.width-48, 760)
        height: Math.min(window.height-48, 570)
        padding: 24
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { radius: 24; color: window.theme.surface; border.color: window.line }
        ColumnLayout {
            anchors.fill: parent
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                Label { text: qsTranslate("Console", "DaOS applications"); color: window.ink; font.pixelSize: 24; font.weight: Font.DemiBold; Layout.fillWidth: true }
                Button { text: qsTranslate("Console", "Close"); onClicked: launcherDialog.close() }
            }
            Label { text: qsTranslate("Console", "Your camera, organized around photography."); color: window.muted; font.pixelSize: 13 }
            GridLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                columns: 3
                rowSpacing: 12
                columnSpacing: 12
                Repeater {
                    model: cameraShell.apps
                    delegate: Button {
                        objectName: "appLaunch-" + modelData.id
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        enabled: modelData.enabled
                        onClicked: { cameraShell.navigate(modelData.id); launcherDialog.close() }
                        background: Rectangle { radius: 20; color: parent.hovered ? window.theme.softAccent : window.theme.raised; border.color: parent.activeFocus ? window.accent : window.line }
                        contentItem: Column {
                            spacing: 12
                            DaOSIcon { width: 28; height: 28; kind: modelData.id; ink: window.accent; anchors.horizontalCenter: parent.horizontalCenter }
                            Label { text: qsTranslate("Console", modelData.title); color: window.ink; font.pixelSize: 14; font.weight: Font.Medium; anchors.horizontalCenter: parent.horizontalCenter }
                        }
                    }
                }
            }
            Label { text: qsTranslate("Console", "HF-Sim · Local apps · No model or cloud required"); color: window.muted; font.pixelSize: 11 }
        }
    }
}
