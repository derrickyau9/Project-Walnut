import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: playback
    objectName: "cameraPlayback"
    property var cameraController: null
    property var apps: null
    property var theme: ({})
    signal closeRequested()

    readonly property color accent: theme && theme.accent ? theme.accent : "#666666"
    readonly property var pageItems: cameraController ? cameraController.libraryItems : []
    readonly property int pageCount: pageItems ? pageItems.length : 0
    readonly property bool loading: cameraController ? cameraController.libraryLoading : false
    readonly property bool canRead: cameraController ? cameraController.canBrowseCaptures : false
    readonly property bool canAct: canRead && !loading && !cameraController.busy
    readonly property string selectedId: cameraController ? cameraController.selectedCaptureId : ""
    readonly property int pageIndex: indexOfCapture(selectedId)
    readonly property bool hasOlderPage: cameraController && cameraController.libraryNextCursor.length > 0
    readonly property bool readFailed: cameraController &&
        (cameraController.libraryMessage.indexOf("could not") >= 0 || cameraController.libraryMessage.indexOf("unavailable") >= 0)
    readonly property bool overlayOpen: infoPopup.visible || exportDialog.visible

    property bool completed: false
    property bool entryActive: false
    property bool entryRefreshNeeded: false
    property bool waitingForPage: false

    function indexOfCapture(requestId) {
        if (!requestId || !pageItems)
            return -1
        for (var index = 0; index < pageItems.length; index++) {
            if (pageItems[index].request_id === requestId)
                return index
        }
        return -1
    }

    function selectRequest(requestId) {
        if (!canAct || indexOfCapture(requestId) < 0)
            return false
        // Retained selection is separate from the original shutter request.
        cameraController.selectCapture(requestId)
        return true
    }

    function selectRelative(offset) {
        var target = pageIndex + offset
        if (pageIndex < 0 || target < 0 || target >= pageCount)
            return
        selectRequest(pageItems[target].request_id)
    }

    function updateEntry() {
        if (!completed || !visible || !entryActive || !cameraController)
            return
        if (entryRefreshNeeded && canAct) {
            entryRefreshNeeded = false
            waitingForPage = true
            cameraController.refreshLibrary()
            return
        }
        if (waitingForPage && !loading && !entryRefreshNeeded) {
            waitingForPage = false
            if (canAct && pageCount > 0 && selectedId.length === 0)
                selectRequest(pageItems[0].request_id)
        }
    }

    function enterPlayback() {
        if (!completed || !visible || entryActive)
            return
        entryActive = true
        entryRefreshNeeded = true
        waitingForPage = false
        updateEntry()
    }

    function refreshNewest() {
        if (!canAct)
            return
        entryRefreshNeeded = false
        waitingForPage = true
        cameraController.refreshLibrary()
    }

    function olderPage() {
        if (!canAct || !hasOlderPage)
            return
        entryRefreshNeeded = false
        waitingForPage = true
        cameraController.nextLibraryPage()
    }

    function validExportPath(value) {
        return value.length > 0 && !/[\x00-\x1f]/.test(value) &&
            /^(?:[A-Za-z]:[\\\/]|\/)/.test(value) && /\.dng$/i.test(value) && value.indexOf("://") < 0
    }

    function closeOverlay() {
        if (exportDialog.visible) {
            exportDialog.close()
            return true
        }
        if (infoPopup.visible) {
            infoPopup.close()
            return true
        }
        return false
    }

    function submitExport() {
        if (!canAct || selectedId.length === 0 || exportDialog.submitted)
            return
        var destination = exportPath.text.trim()
        if (destination.length === 0 && apps && apps.exportDirectory.length > 0) {
            var safeId = selectedId.replace(/[^A-Za-z0-9_.-]/g, "_")
            destination = apps.exportDirectory + "/photo-" + safeId + "-" + Date.now() + ".dng"
            exportPath.text = destination
        }
        if (!validExportPath(destination)) {
            exportDialog.localMessage = "Choose a new absolute local path ending in .dng."
            return
        }
        exportDialog.localMessage = ""
        exportDialog.submitted = true
        // One explicit copy request. No capture and no automatic retry.
        cameraController.exportSelected(destination)
    }

    onCameraControllerChanged: {
        if (completed && visible) {
            entryActive = false
            enterPlayback()
        }
    }
    onVisibleChanged: {
        if (!completed)
            return
        if (visible) {
            enterPlayback()
        } else {
            entryActive = false
            entryRefreshNeeded = false
            waitingForPage = false
            infoPopup.close()
            exportDialog.close()
        }
    }
    Component.onCompleted: {
        completed = true
        enterPlayback()
    }
    Connections {
        target: playback.cameraController
        function onChanged() { playback.updateEntry() }
    }

    focus: visible
    Keys.onLeftPressed: {
        if (!infoPopup.visible && !exportDialog.visible) {
            selectRelative(-1)
            event.accepted = true
        }
    }
    Keys.onRightPressed: {
        if (!infoPopup.visible && !exportDialog.visible) {
            selectRelative(1)
            event.accepted = true
        }
    }
    Keys.onEscapePressed: {
        if (!closeOverlay())
            closeRequested()
        event.accepted = true
    }

    Rectangle { anchors.fill: parent; color: "#000000" }

    Item {
        id: imageStage
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: topBar.bottom
        anchors.bottom: bottomBar.top

        Image {
            id: reviewImage
            objectName: "playbackReviewImage"
            anchors.fill: parent
            source: playback.cameraController ? playback.cameraController.reviewUrl : ""
            visible: source.toString().length > 0 && status !== Image.Error
            fillMode: Image.PreserveAspectFit
            smooth: true
            cache: false
            Accessible.name: "Selected retained synthetic DNG display conversion; not live preview."
        }
        Text {
            anchors.centerIn: parent
            width: parent.width - 48
            visible: reviewImage.source.toString().length === 0 || reviewImage.status === Image.Error
            text: playback.loading ? "READING PHOTO…" :
                  playback.readFailed || !playback.canRead || reviewImage.status === Image.Error ? "PHOTO UNAVAILABLE" :
                  playback.pageCount > 0 ? "SELECT A PHOTO" : "NO SAVED PHOTOS"
            textFormat: Text.PlainText
            color: "#cccccc"
            font.pixelSize: 15
            font.letterSpacing: 1.1
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }
    }

    Rectangle {
        id: topBar
        anchors.top: parent.top
        width: parent.width
        height: 48
        color: "#111111"
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#444444" }

        Button {
            id: backButton
            objectName: "playbackBackButton"
            x: 8
            anchors.verticalCenter: parent.verticalCenter
            width: 94
            height: 44
            onClicked: playback.closeRequested()
            Accessible.name: "Back to camera"
            background: Rectangle { color: backButton.down ? "#333333" : backButton.hovered ? "#222222" : "#111111"; radius: 3; border.width: backButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            contentItem: Row {
                spacing: 6
                anchors.centerIn: parent
                DaOSIcon { width: 20; height: 20; kind: "back"; ink: "#ffffff"; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "BACK"; color: "#ffffff"; font.pixelSize: 12; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
            }
        }
        Text {
            x: 118
            anchors.verticalCenter: parent.verticalCenter
            text: "PLAY"
            color: "#ffffff"
            font.pixelSize: 13
            font.bold: true
            font.letterSpacing: 1.8
        }
        Text {
            anchors.centerIn: parent
            text: playback.loading ? "READING…" : playback.pageIndex >= 0 ?
                  (playback.pageIndex + 1) + " / " + playback.pageCount + "  ·  THIS PAGE" : ""
            textFormat: Text.PlainText
            color: "#cccccc"
            font.pixelSize: 11
        }
        Rectangle {
            anchors.right: parent.right
            anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            width: 62
            height: 24
            color: "#222222"
            radius: 3
            Text { anchors.centerIn: parent; text: "HF-Sim"; color: "#cccccc"; font.pixelSize: 10 }
        }
    }

    Rectangle {
        id: bottomBar
        anchors.bottom: parent.bottom
        width: parent.width
        height: 56
        color: "#111111"
        Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: "#444444" }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 12
            anchors.topMargin: 6
            anchors.bottomMargin: 6
            spacing: 8
            Button {
                id: previousButton
                objectName: "playbackPreviousButton"
                Layout.preferredWidth: 94
                Layout.preferredHeight: 44
                text: "‹  PREV"
                enabled: playback.canAct && playback.pageIndex > 0
                onClicked: playback.selectRelative(-1)
                Accessible.name: "Previous photo in this loaded page"
                contentItem: Text { text: previousButton.text; textFormat: Text.PlainText; color: previousButton.enabled ? "#ffffff" : "#777777"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: previousButton.down ? "#333333" : previousButton.hovered && previousButton.enabled ? "#222222" : "#111111"; radius: 3; border.width: previousButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
            Button {
                id: nextButton
                objectName: "playbackNextButton"
                Layout.preferredWidth: 94
                Layout.preferredHeight: 44
                text: "NEXT  ›"
                enabled: playback.canAct && playback.pageIndex >= 0 && playback.pageIndex < playback.pageCount - 1
                onClicked: playback.selectRelative(1)
                Accessible.name: "Next photo in this loaded page"
                contentItem: Text { text: nextButton.text; textFormat: Text.PlainText; color: nextButton.enabled ? "#ffffff" : "#777777"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: nextButton.down ? "#333333" : nextButton.hovered && nextButton.enabled ? "#222222" : "#111111"; radius: 3; border.width: nextButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
            Item { Layout.fillWidth: true }
            Button {
                id: infoButton
                objectName: "playbackInfoButton"
                Layout.preferredWidth: 76
                Layout.preferredHeight: 44
                text: "INFO"
                enabled: playback.selectedId.length > 0
                onClicked: infoPopup.open()
                contentItem: Text { text: infoButton.text; color: infoButton.enabled ? "#ffffff" : "#777777"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: infoButton.down ? "#333333" : infoButton.hovered && infoButton.enabled ? "#222222" : "#111111"; radius: 3; border.width: infoButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
            Button {
                id: exportButton
                objectName: "playbackExportButton"
                Layout.preferredWidth: 88
                Layout.preferredHeight: 44
                text: "EXPORT"
                enabled: playback.canAct && playback.selectedId.length > 0
                onClicked: exportDialog.open()
                Accessible.name: "Export a new copy of the selected DNG"
                contentItem: Text { text: exportButton.text; color: exportButton.enabled ? "#ffffff" : "#777777"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: exportButton.down ? "#333333" : exportButton.hovered && exportButton.enabled ? "#222222" : "#111111"; radius: 3; border.width: exportButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
            Button {
                id: olderButton
                objectName: "playbackOlderPageButton"
                Layout.preferredWidth: 98
                Layout.preferredHeight: 44
                text: "OLDER PAGE"
                visible: playback.hasOlderPage
                enabled: playback.canAct
                onClicked: playback.olderPage()
                contentItem: Text { text: olderButton.text; color: olderButton.enabled ? "#ffffff" : "#777777"; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: olderButton.down ? "#333333" : olderButton.hovered && olderButton.enabled ? "#222222" : "#111111"; radius: 3; border.width: olderButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
            Button {
                id: refreshButton
                objectName: "playbackRefreshButton"
                Layout.preferredWidth: 86
                Layout.preferredHeight: 44
                text: "REFRESH"
                enabled: playback.canAct
                onClicked: playback.refreshNewest()
                Accessible.name: "Refresh newest saved photographs"
                contentItem: Text { text: refreshButton.text; color: refreshButton.enabled ? "#cccccc" : "#777777"; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: refreshButton.down ? "#333333" : refreshButton.hovered && refreshButton.enabled ? "#222222" : "#111111"; radius: 3; border.width: refreshButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
        }
    }

    Popup {
        id: infoPopup
        objectName: "playbackInfoPopup"
        parent: playback
        x: (playback.width - width) / 2
        y: (playback.height - height) / 2
        width: Math.min(580, playback.width - 48)
        height: Math.min(352, playback.height - 64)
        modal: true
        focus: true
        padding: 20
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: "#111111"; radius: 8; border.color: "#444444" }
        contentItem: ColumnLayout {
            spacing: 14
            Text { text: "PHOTO INFORMATION"; color: "#ffffff"; font.pixelSize: 14; font.bold: true; font.letterSpacing: 0.6 }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                TextArea {
                    objectName: "playbackInfoText"
                    text: playback.cameraController ? "Capture ID: " + playback.selectedId + "\n\n" + playback.cameraController.selectedCaptureInfo : ""
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.WrapAnywhere
                    color: "#cccccc"
                    font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                    font.pixelSize: 12
                    background: null
                }
            }
            Text { Layout.fillWidth: true; text: "HF-Sim · retained DNG display conversion\nSynthetic pixels; uncalibrated color. This is not live preview."; color: "#aaaaaa"; font.pixelSize: 11; wrapMode: Text.WordWrap }
            Button {
                id: infoCloseButton
                objectName: "playbackInfoCloseButton"
                Layout.alignment: Qt.AlignRight
                Layout.preferredWidth: 104
                Layout.preferredHeight: 44
                text: "CLOSE"
                onClicked: infoPopup.close()
                contentItem: Text { text: infoCloseButton.text; color: "#ffffff"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: infoCloseButton.down ? "#333333" : "#222222"; radius: 4; border.width: infoCloseButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
            }
        }
    }

    Popup {
        id: exportDialog
        objectName: "playbackExportDialog"
        property bool submitted: false
        property string localMessage: ""
        parent: playback
        x: (playback.width - width) / 2
        y: (playback.height - height) / 2
        width: Math.min(640, playback.width - 48)
        height: Math.min(340, playback.height - 64)
        modal: true
        focus: true
        padding: 20
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: {
            submitted = false
            localMessage = ""
            exportPath.text = ""
            exportPath.forceActiveFocus()
        }
        background: Rectangle { color: "#111111"; radius: 8; border.color: "#444444" }
        contentItem: ColumnLayout {
            spacing: 14
            Text { text: "EXPORT DNG COPY"; color: "#ffffff"; font.pixelSize: 14; font.bold: true; font.letterSpacing: 0.6 }
            Text { Layout.fillWidth: true; text: "New absolute local .dng path. Leave blank for a new file in Exports.\nThe original is retained; existing files are never overwritten."; color: "#cccccc"; font.pixelSize: 12; wrapMode: Text.WordWrap }
            TextField {
                id: exportPath
                objectName: "playbackExportPathInput"
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                placeholderText: "New local .dng path (optional)"
                selectByMouse: true
                color: "#ffffff"
                selectionColor: playback.accent
                placeholderTextColor: "#aaaaaa"
                font.pixelSize: 13
                onTextEdited: { exportDialog.submitted = false; exportDialog.localMessage = "" }
                background: Rectangle { color: "#222222"; radius: 4; border.color: exportPath.activeFocus ? "#ffffff" : "#444444" }
                Accessible.name: "New absolute local DNG export path"
            }
            Text {
                objectName: "playbackExportMessage"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: exportDialog.localMessage.length > 0 ? exportDialog.localMessage :
                      exportDialog.submitted && playback.cameraController ? playback.cameraController.exportMessage : ""
                textFormat: Text.PlainText
                color: "#cccccc"
                font.pixelSize: 11
                wrapMode: Text.WrapAnywhere
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    id: exportCancelButton
                    objectName: "playbackExportCancelButton"
                    Layout.preferredWidth: 108
                    Layout.preferredHeight: 44
                    text: "CLOSE"
                    onClicked: exportDialog.close()
                    contentItem: Text { text: exportCancelButton.text; color: "#ffffff"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { color: exportCancelButton.down ? "#333333" : "#222222"; radius: 4; border.width: exportCancelButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
                }
                Button {
                    id: exportConfirmButton
                    objectName: "playbackExportConfirmButton"
                    Layout.preferredWidth: 132
                    Layout.preferredHeight: 44
                    text: "EXPORT COPY"
                    enabled: playback.canAct && playback.selectedId.length > 0 && !exportDialog.submitted
                    onClicked: playback.submitExport()
                    Accessible.description: "Submit one explicit copy request. Does not capture or retry."
                    contentItem: Text { text: exportConfirmButton.text; color: exportConfirmButton.enabled ? "#ffffff" : "#aaaaaa"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { color: exportConfirmButton.enabled ? exportConfirmButton.down ? "#777777" : "#666666" : "#333333"; radius: 4; border.width: exportConfirmButton.activeFocus ? 1 : 0; border.color: "#ffffff" }
                }
            }
        }
    }
}
