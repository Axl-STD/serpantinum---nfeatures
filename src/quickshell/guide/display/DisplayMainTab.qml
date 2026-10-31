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
                implicitHeight: diagnosticRow.implicitHeight + rootObj.s(20)
                radius: ThemeBackend.borderRadius
                color: Qt.alpha(ThemeBackend.surface0, 0.5)
                RowLayout {
                    id: diagnosticRow
                    anchors.fill: parent
                    anchors.margins: rootObj.s(10)
                    spacing: rootObj.s(10)
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Development evidence: " + displayTabRoot.diagnosticAssessment
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(12)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: displayTabRoot.diagnosticMessage
                            wrapMode: Text.WordWrap
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(11)
                        }
                    }
                    Button {
                        text: displayTabRoot.diagnosticBusy ? "Checking..." : "Check evidence"
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
