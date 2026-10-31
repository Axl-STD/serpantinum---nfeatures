import QtQuick
import QtQuick.Layouts
import "../../"
import "../../reusables"

Rectangle {
    id: controls
    required property var tab
    readonly property var rootObj: tab.rootObj
    readonly property var names: tab.connectedDisplayNames
    readonly property var sourceOptions: names.map(name => {
        let mon = tab.monitorsList.find(m => m.name === name);
        return mon && mon.description ? name + " · " + mon.description : name;
    })
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + rootObj.s(18)
    visible: names.length > 0
    radius: ThemeBackend.borderRadius
    color: Qt.alpha(ThemeBackend.surface0, 0.4)
    border.width: ThemeBackend.liquidGlass ? 0 : 1
    border.color: Qt.alpha(ThemeBackend.surface1, 0.5)

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: rootObj.s(9)
        spacing: rootObj.s(7)

        RowLayout {
            Layout.fillWidth: true
            spacing: rootObj.s(8)
            Text {
                Layout.fillWidth: true
                text: I18n.t("guide.display.arrangement.mode")
                color: ThemeBackend.text
                font.family: ThemeBackend.fontFamily
                font.pixelSize: rootObj.s(12)
                elide: Text.ElideRight
            }
            Dropdown {
                Layout.preferredWidth: rootObj.s(190)
                Layout.maximumWidth: rootObj.s(230)
                implicitHeight: rootObj.s(32)
                options: [I18n.t("guide.display.arrangement.extend"), I18n.t("guide.display.arrangement.mirror")]
                currentIndex: tab.previewDisplayMode === "mirror" ? 1 : 0
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
                    tab.previewDisplayMode = index === 1 ? "mirror" : "extend";
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: tab.previewDisplayMode === "mirror" && names.length > 1
            spacing: rootObj.s(8)
            Text {
                Layout.fillWidth: true
                text: I18n.t("guide.display.arrangement.source")
                color: ThemeBackend.text
                font.family: ThemeBackend.fontFamily
                font.pixelSize: rootObj.s(12)
                elide: Text.ElideRight
            }
            Dropdown {
                Layout.preferredWidth: rootObj.s(190)
                Layout.maximumWidth: rootObj.s(230)
                implicitHeight: rootObj.s(32)
                options: controls.sourceOptions
                currentIndex: Math.max(0, names.indexOf(tab.previewMirrorSource))
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
                    if (index >= 0 && index < names.length)
                        tab.previewMirrorSource = names[index];
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: rootObj.s(8)
            Text {
                Layout.fillWidth: true
                text: I18n.t("guide.display.arrangement.primary") + ": " + (tab.preferredPrimaryMonitor || "—")
                color: ThemeBackend.subtext0
                font.family: ThemeBackend.fontFamily
                font.pixelSize: rootObj.s(11)
                elide: Text.ElideRight
            }
            Rectangle {
                visible: tab.selectedMonitorName !== "" && tab.selectedMonitorName !== tab.preferredPrimaryMonitor
                implicitWidth: primaryLabel.implicitWidth + rootObj.s(18)
                implicitHeight: rootObj.s(29)
                radius: ThemeBackend.borderRadius
                color: primaryMouse.containsMouse ? ThemeBackend.surface2 : ThemeBackend.surface1
                Text {
                    id: primaryLabel
                    anchors.centerIn: parent
                    text: I18n.t("guide.display.arrangement.make_primary")
                    font.family: ThemeBackend.fontFamily
                    font.pixelSize: rootObj.s(11)
                    color: ThemeBackend.text
                }
                MouseArea {
                    id: primaryMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: tab.choosePrimaryMonitor(tab.selectedMonitorName)
                }
            }
        }
        Text {
            Layout.fillWidth: true
            text: I18n.t("guide.display.arrangement.preview")
            color: ThemeBackend.subtext0
            font.family: ThemeBackend.fontFamily
            font.pixelSize: rootObj.s(10)
            wrapMode: Text.WordWrap
        }
    }
}
