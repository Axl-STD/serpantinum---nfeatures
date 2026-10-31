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

            // v0.5.60: planning and evidence are independent read-only summaries.
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: overviewColumn.implicitHeight + rootObj.s(24)
                radius: ThemeBackend.borderRadius
                color: Qt.alpha(ThemeBackend.surface0, 0.5)
                border.width: 1
                border.color: ThemeBackend.surface2
                ColumnLayout {
                    id: overviewColumn
                    anchors.fill: parent
                    anchors.margins: rootObj.s(12)
                    spacing: rootObj.s(7)
                    Text {
                        text: I18n.t("guide.display.overview.title", "Display status")
                        color: ThemeBackend.text
                        font.family: ThemeBackend.fontFamily
                        font.bold: true
                        font.pixelSize: rootObj.s(13)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: rootObj.s(10)
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: rootObj.s(2)
                            Text {
                                text: I18n.t("guide.display.overview.pending", "Pending display changes")
                                color: ThemeBackend.text
                                font.family: ThemeBackend.fontFamily
                                font.pixelSize: rootObj.s(12)
                            }
                            Text {
                                text: I18n.t("guide.display.overview.preview", "Preview only · Not applied or saved")
                                color: ThemeBackend.text
                                opacity: 0.7
                                font.family: ThemeBackend.fontFamily
                                font.pixelSize: rootObj.s(10)
                            }
                        }
                        Text {
                            text: String(displayTabRoot.pendingDisplayCount)
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.bold: true
                            font.pixelSize: rootObj.s(13)
                        }
                        Button {
                            visible: displayTabRoot.pendingDisplayCount > 0
                            text: I18n.t("guide.display.overview.discard", "Discard")
                            onClicked: {
                                displayTabRoot.discardDisplayChanges();
                                displayTabRoot.reloadMonitors();
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: rootObj.s(8)
                        Text {
                            Layout.fillWidth: true
                            text: I18n.t("guide.display.overview.detector", "Monitor detection")
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(12)
                        }
                        Text {
                            text: I18n.t("guide.display.overview.detector_status." + displayTabRoot.monitorDetectionStatus, "Detection unavailable")
                                  + (displayTabRoot.monitorTopologyNoticeKey !== ""
                                     ? " · " + I18n.t("guide.display.overview.topology_notice." + displayTabRoot.monitorTopologyNoticeKey, "Last known topology")
                                     : "")
                            color: displayTabRoot.monitorDetectionStatus === "blocked" || displayTabRoot.monitorDetectionStatus === "failed" ? ThemeBackend.mauve : ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(11)
                            wrapMode: Text.WordWrap
                            horizontalAlignment: Text.AlignRight
                            Layout.maximumWidth: rootObj.s(210)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: displayTabRoot.monitorLastSuccessfulAgeMinutes >= 0
                        spacing: rootObj.s(8)
                        Text {
                            Layout.fillWidth: true
                            text: I18n.t("guide.display.overview.last_successful_detection", "Last successful detection")
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(12)
                        }
                        Text {
                            text: displayTabRoot.monitorLastSuccessfulAgeMinutes === 0
                                  ? I18n.t("guide.display.overview.age_just_now", "Just now")
                                  : I18n.t("guide.display.overview.age_minutes_ago", "%1 min ago")
                                        .replace("%1", String(displayTabRoot.monitorLastSuccessfulAgeMinutes))
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(11)
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: I18n.t("guide.display.overview.evidence", "Evidence status")
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(12)
                        }
                        Text {
                            text: I18n.t("guide.display.diagnostic.status." + displayTabRoot.diagnosticStatusKey, "Diagnostic status")
                            color: displayTabRoot.diagnosticStatusKey === "conflict" || displayTabRoot.diagnosticStatusKey === "failed" ? ThemeBackend.mauve : ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(11)
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
                        enabled: !displayTabRoot.diagnosticBusy && !displayTabRoot.diagnosticBlocked
                        onClicked: displayTabRoot.requestDisplayDiagnostic()
                    }
                    Button {
                        visible: displayTabRoot.diagnosticBlocked
                        text: I18n.t("guide.display.diagnostic.recover", "Reset diagnostic")
                        enabled: displayTabRoot.diagnosticRecoveryAvailable
                        onClicked: displayTabRoot.recoverDisplayDiagnostic()
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
