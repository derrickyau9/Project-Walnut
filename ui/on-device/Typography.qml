import QtQuick 2.15

// A single neutral sans-serif hierarchy for the small camera display.
QtObject {
    readonly property string uiFamily: typeof cameraUIFont !== "undefined" ? cameraUIFont : "Noto Sans CJK SC"
    readonly property string titleFamily: typeof cameraTitleFont !== "undefined" ? cameraTitleFont : "Noto Sans CJK SC"
    readonly property string numericFamily: uiFamily
    readonly property int titleSize: 24
    readonly property int sectionSize: 22
    readonly property int bodySize: 18
    readonly property int captionSize: 14
    readonly property int numericSize: 28
    readonly property real titleTracking: 1.2
    readonly property real bodyTracking: 0.1
    readonly property real numericTracking: 0.25
}
