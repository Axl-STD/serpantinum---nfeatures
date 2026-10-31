import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import "../../"

DisplayMainState {
    id: displayTabRoot
    subTabIndex: 0

    Flickable {
        anchors.fill: parent
        anchors.topMargin: rootObj.s(8)
        anchors.leftMargin: rootObj.s(8)
        anchors.rightMargin: rootObj.s(8)
        anchors.bottomMargin: rootObj.s(4)
        contentHeight: settingsCol.implicitHeight + rootObj.s(16)
        contentWidth: width
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar {
            active: parent.moving || parent.movingVertically
            width: rootObj.s(4)
            policy: ScrollBar.AsNeeded
            contentItem: Rectangle {
                implicitWidth: rootObj.s(4)
                radius: rootObj.s(2)
                color: ThemeBackend.surface2
            }
        }

        ColumnLayout {
            id: settingsCol
            width: parent.width - (parent.contentHeight > parent.height ? rootObj.s(6) : 0)
            spacing: rootObj.s(12)

            Rectangle {
                Layout.fillWidth: true
                visible: displayTabRoot.pendingDisplayCount > 0
                implicitHeight: pendingRow.implicitHeight + rootObj.s(20)
                radius: ThemeBackend.borderRadius
                color: Qt.alpha(ThemeBackend.surface0, 0.5)
                border.width: 1
                border.color: ThemeBackend.mauve
                RowLayout {
                    id: pendingRow
                    anchors.fill: parent
                    anchors.margins: rootObj.s(10)
                    spacing: rootObj.s(10)
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Pending display changes (preview only) — not applied or saved"
                        color: ThemeBackend.text
                        font.family: ThemeBackend.fontFamily
                        font.pixelSize: rootObj.s(12)
                    }
                    Button {
                        text: "Discard"
                        onClicked: {
                            displayTabRoot.discardDisplayChanges();
                            displayTabRoot.reloadMonitors();
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: diagnosticRow.implicitHeight + rootObj.s(24)
                radius: ThemeBackend.borderRadius
                color: Qt.alpha(ThemeBackend.surface0, 0.5)
                border.width: 1
                border.color: displayTabRoot.diagnosticStatusKey === "conflict" || displayTabRoot.diagnosticStatusKey === "failed" ? ThemeBackend.mauve : ThemeBackend.surface2
                RowLayout {
                    id: diagnosticRow
                    anchors.fill: parent
                    anchors.margins: rootObj.s(12)
                    spacing: rootObj.s(12)
                    Rectangle {
                        Layout.preferredWidth: rootObj.s(5)
                        Layout.fillHeight: true
                        radius: rootObj.s(3)
                        color: displayTabRoot.diagnosticStatusKey === "conflict" || displayTabRoot.diagnosticStatusKey === "failed" ? ThemeBackend.mauve : ThemeBackend.surface2
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: rootObj.s(4)
                        Text {
                            text: I18n.t("guide.display.diagnostic.title", "Display diagnostics")
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.bold: true
                            font.pixelSize: rootObj.s(13)
                        }
                        Text {
                            text: I18n.t("guide.display.diagnostic.status." + displayTabRoot.diagnosticStatusKey, "Diagnostic status")
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(12)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: displayTabRoot.diagnosticMessage
                            wrapMode: Text.WordWrap
                            color: ThemeBackend.text
                            opacity: 0.8
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(11)
                        }
                        Text {
                            text: I18n.t("guide.display.diagnostic.read_only", "Development evidence · Read only")
                            color: ThemeBackend.text
                            opacity: 0.65
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(10)
                        }
                    }
                    Button {
                        text: displayTabRoot.diagnosticBusy ? I18n.t("guide.display.diagnostic.checking", "Checking...") : I18n.t("guide.display.diagnostic.check", "Check evidence")
                        enabled: !displayTabRoot.diagnosticBusy
                        onClicked: displayTabRoot.requestDisplayDiagnostic()
                    }
                }
            }

            DisplayLayoutMap {
                Layout.fillWidth: true
                tab: displayTabRoot
            }

            DisplayArrangementControls {
                Layout.fillWidth: true
                tab: displayTabRoot
            }

            Repeater {
                model: displayTabRoot.monitorsList
                delegate: DisplayMonitorCard { tab: displayTabRoot }
            }
        }
    }
}
