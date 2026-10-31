import QtQuick
import QtQuick.Layouts
import "../../"

Rectangle {
    id: layoutMap
    required property var tab
    readonly property var rootObj: tab.rootObj
    readonly property var activeMonitors: tab.monitorsList.filter(m => m.active && Number(m.dimensions.split("x")[0]) > 0).slice().sort((a, b) => (Number(a.x) - Number(b.x)) || (Number(a.y) - Number(b.y)) || a.name.localeCompare(b.name))
    readonly property real minX: activeMonitors.length ? Math.min.apply(null, activeMonitors.map(m => Number(m.x) || 0)) : 0
    readonly property real minY: activeMonitors.length ? Math.min.apply(null, activeMonitors.map(m => Number(m.y) || 0)) : 0
    readonly property real maxX: activeMonitors.length ? Math.max.apply(null, activeMonitors.map(m => (Number(m.x) || 0) + logicalWidth(m))) : 1
    readonly property real maxY: activeMonitors.length ? Math.max.apply(null, activeMonitors.map(m => (Number(m.y) || 0) + logicalHeight(m))) : 1
    readonly property real extentX: Math.max(1, maxX - minX)
    readonly property real extentY: Math.max(1, maxY - minY)
    readonly property real fitScale: Math.max(0.001, Math.min(Math.max(1, mapArea.width - rootObj.s(24)) / extentX, Math.max(1, mapArea.height - rootObj.s(24)) / extentY))

    function logicalWidth(m) {
        let parts = m.dimensions.split("x");
        let rotated = m.transform === 1 || m.transform === 3 || m.transform === 5 || m.transform === 7;
        return (Number(parts[rotated ? 1 : 0]) || 1) / Math.max(0.1, Number(m.scale) || 1);
    }
    function logicalHeight(m) {
        let parts = m.dimensions.split("x");
        let rotated = m.transform === 1 || m.transform === 3 || m.transform === 5 || m.transform === 7;
        return (Number(parts[rotated ? 0 : 1]) || 1) / Math.max(0.1, Number(m.scale) || 1);
    }

    Layout.fillWidth: true
    implicitHeight: rootObj.s(230)
    radius: ThemeBackend.borderRadius
    color: Qt.alpha(ThemeBackend.surface0, 0.4)
    border.width: ThemeBackend.liquidGlass ? 0 : 1
    border.color: Qt.alpha(ThemeBackend.surface1, 0.5)
    visible: tab.monitorsList.length > 0

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: rootObj.s(12)
        spacing: rootObj.s(6)
        Text {
            Layout.fillWidth: true
            text: I18n.t("guide.display.layout.title", "Display arrangement")
            color: ThemeBackend.text
            font.family: ThemeBackend.fontFamily
            font.pixelSize: rootObj.s(14)
            font.bold: true
            elide: Text.ElideRight
        }
        Item {
            id: mapArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            // Subtle grid uses the existing theme colors; it is purely decorative.
            Canvas {
                anchors.fill: parent
                onPaint: {
                    let ctx = getContext("2d");
                    ctx.clearRect(0, 0, width, height);
                    ctx.strokeStyle = Qt.rgba(ThemeBackend.subtext0.r, ThemeBackend.subtext0.g, ThemeBackend.subtext0.b, 0.16);
                    ctx.lineWidth = 1;
                    let step = Math.max(12, rootObj.s(20));
                    for (let x = 0; x <= width; x += step) {
                        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
                    }
                    for (let y = 0; y <= height; y += step) {
                        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
                    }
                }
            }
            Repeater {
                model: layoutMap.activeMonitors
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    readonly property bool selected: layoutMap.tab.selectedMonitorName === modelData.name
                    width: Math.max(1, layoutMap.logicalWidth(modelData) * layoutMap.fitScale)
                    height: Math.max(1, layoutMap.logicalHeight(modelData) * layoutMap.fitScale)
                    x: (mapArea.width - layoutMap.extentX * layoutMap.fitScale) / 2 + ((Number(modelData.x) || 0) - layoutMap.minX) * layoutMap.fitScale
                    y: (mapArea.height - layoutMap.extentY * layoutMap.fitScale) / 2 + ((Number(modelData.y) || 0) - layoutMap.minY) * layoutMap.fitScale
                    radius: rootObj.s(5)
                    color: selected ? ThemeBackend.surface2 : ThemeBackend.surface1
                    border.color: selected ? ThemeBackend.mauve : ThemeBackend.subtext0
                    border.width: selected ? rootObj.s(2) : 1
                    clip: true
                    Column {
                        anchors.centerIn: parent
                        width: parent.width - rootObj.s(8)
                        spacing: rootObj.s(2)
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: (index + 1).toString()
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.bold: true
                            font.pixelSize: rootObj.s(19)
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            visible: layoutMap.tab.preferredPrimaryMonitor === modelData.name
                            text: "★"
                            color: ThemeBackend.mauve
                            font.pixelSize: rootObj.s(12)
                            font.family: ThemeBackend.fontFamily
                        }
                        Text {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            text: modelData.name
                            color: ThemeBackend.text
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(10)
                        }
                        Text {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            text: modelData.dimensions + " · " + modelData.framerate + " Hz"
                            color: ThemeBackend.subtext0
                            font.family: ThemeBackend.fontFamily
                            font.pixelSize: rootObj.s(9)
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: layoutMap.tab.selectedMonitorName = modelData.name
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            text: I18n.t("guide.display.layout.preview", "Select a display to inspect it. Arrangement preview only.")
            color: ThemeBackend.subtext0
            font.family: ThemeBackend.fontFamily
            font.pixelSize: rootObj.s(10)
            elide: Text.ElideRight
        }
    }
}
