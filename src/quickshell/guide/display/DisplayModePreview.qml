import QtQuick
import QtQuick.Layouts
import "../../"
import "../../reusables"

Rectangle {
    id: modeRow
    required property var tab
    required property var monitor
    readonly property var rootObj: tab.rootObj
    readonly property var modes: tab.parseAvailableModes(monitor)
    readonly property var resolutions: {
        let result = [];
        for (let i = 0; i < modes.length; i++)
            if (result.indexOf(modes[i].resolution) < 0) result.push(modes[i].resolution);
        return result;
    }
    property string chosenResolution: ""
    property string chosenModeKey: ""
    // Preview state only. Hyprland transform 1/3/5/7 is portrait.
    property bool previewPortrait: false
    readonly property bool currentPortrait: {
        let t = Number(monitor && monitor.transform !== undefined ? monitor.transform : 0);
        return t === 1 || t === 3 || t === 5 || t === 7;
    }
    readonly property var rates: {
        let result = [];
        for (let i = 0; i < modes.length; i++)
            if (modes[i].resolution === chosenResolution) result.push(modes[i]);
        return result;
    }
    function synchronize() {
        previewPortrait = currentPortrait;
        let options = resolutions || [];
        let resolution = monitor && monitor.dimensions ? monitor.dimensions : "";
        chosenResolution = options.indexOf(resolution) >= 0 ? resolution : (options.length ? options[0] : "");
        let target = Number(monitor && monitor.framerate ? monitor.framerate : 0);
        let nearest = null;
        for (let i = 0; i < modes.length; i++) {
            let mode = modes[i];
            if (mode.resolution !== chosenResolution) continue;
            if (!nearest || Math.abs(mode.hz - target) < Math.abs(nearest.hz - target)) nearest = mode;
        }
        chosenModeKey = nearest ? nearest.key : "";
    }
    onMonitorChanged: synchronize()
    onModesChanged: synchronize()
    Component.onCompleted: synchronize()

    Layout.fillWidth: true
    Layout.preferredHeight: visible ? content.implicitHeight + rootObj.s(14) : 0
    implicitHeight: visible ? content.implicitHeight + rootObj.s(14) : 0
    visible: modes.length > 0
    radius: rootObj.s(12)
    color: Qt.alpha(ThemeBackend.surface1, 0.35)

    GridLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: rootObj.s(7)
        columns: width >= rootObj.s(420) ? 2 : 1
        columnSpacing: rootObj.s(10)
        rowSpacing: rootObj.s(5)

        RowLayout {
            Layout.fillWidth: true
            spacing: rootObj.s(6)
            Text {
                text: I18n.t("guide.display.resolution.title", "Resolution")
                color: ThemeBackend.text
                font.family: ThemeBackend.fontFamily
                font.pixelSize: rootObj.s(11)
                Layout.preferredWidth: rootObj.s(74)
                elide: Text.ElideRight
            }
            Dropdown {
                Layout.fillWidth: true
                Layout.minimumWidth: rootObj.s(95)
                implicitHeight: rootObj.s(30)
                options: modeRow.resolutions
                currentIndex: Math.max(0, modeRow.resolutions.indexOf(modeRow.chosenResolution))
                fuzzySearch: false
                accentColor: ThemeBackend.mauve
                baseColor: ThemeBackend.surface0
                hoverColor: ThemeBackend.surface1
                dropdownColor: ThemeBackend.surface0
                borderColor: Qt.alpha(ThemeBackend.surface2, 0.6)
                textColor: ThemeBackend.text
                activeTextColor: ThemeBackend.crust
                cornerRadius: ThemeBackend.borderRadius
                fontPixelSize: rootObj.s(11)
                onSelected: function(index, value) {
                    modeRow.chosenResolution = value;
                    modeRow.chosenModeKey = modeRow.rates.length ? modeRow.rates[0].key : "";
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: rootObj.s(6)
            Text {
                text: I18n.t("guide.display.refresh.title", "Refresh rate")
                color: ThemeBackend.text
                font.family: ThemeBackend.fontFamily
                font.pixelSize: rootObj.s(11)
                Layout.preferredWidth: rootObj.s(78)
                elide: Text.ElideRight
            }
            Dropdown {
                Layout.fillWidth: true
                Layout.minimumWidth: rootObj.s(95)
                implicitHeight: rootObj.s(30)
                options: modeRow.rates.map(m => m.hz.toFixed(2) + " Hz")
                currentIndex: Math.max(0, modeRow.rates.findIndex(m => m.key === modeRow.chosenModeKey))
                fuzzySearch: false
                accentColor: ThemeBackend.mauve
                baseColor: ThemeBackend.surface0
                hoverColor: ThemeBackend.surface1
                dropdownColor: ThemeBackend.surface0
                borderColor: Qt.alpha(ThemeBackend.surface2, 0.6)
                textColor: ThemeBackend.text
                activeTextColor: ThemeBackend.crust
                cornerRadius: ThemeBackend.borderRadius
                fontPixelSize: rootObj.s(11)
                onSelected: function(index, value) {
                    if (modeRow.rates[index]) modeRow.chosenModeKey = modeRow.rates[index].key;
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: rootObj.s(6)
            Text {
                text: "Orientation (preview)"
                color: ThemeBackend.text
                font.family: ThemeBackend.fontFamily
                font.pixelSize: rootObj.s(11)
                Layout.preferredWidth: rootObj.s(78)
                elide: Text.ElideRight
            }
            Dropdown {
                Layout.fillWidth: true
                Layout.minimumWidth: rootObj.s(95)
                implicitHeight: rootObj.s(30)
                options: ["Landscape", "Portrait"]
                currentIndex: modeRow.previewPortrait ? 1 : 0
                fuzzySearch: false
                accentColor: ThemeBackend.mauve
                baseColor: ThemeBackend.surface0
                hoverColor: ThemeBackend.surface1
                dropdownColor: ThemeBackend.surface0
                borderColor: Qt.alpha(ThemeBackend.surface2, 0.6)
                textColor: ThemeBackend.text
                cornerRadius: ThemeBackend.borderRadius
                fontPixelSize: rootObj.s(11)
                onSelected: function(index, value) {
                    modeRow.previewPortrait = index === 1;
                }
            }
        }
    }
}
