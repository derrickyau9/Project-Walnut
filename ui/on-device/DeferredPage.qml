import QtQuick 2.15

// Keep the first frame free of off-screen controls. Retain each page after its
// first visit, including editor state, and supply dependencies before completion.
Loader {
    id: page
    property bool shown: false
    property url pageSource
    property var initialProperties: ({})
    visible: shown
    function ensure() {
        if (source.toString().length === 0)
            setSource(pageSource, initialProperties)
        return item
    }
    onShownChanged: if (shown) ensure()
    Component.onCompleted: if (shown) ensure()
}
