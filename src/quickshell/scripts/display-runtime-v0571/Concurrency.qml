import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    property bool inFlight: false
    property bool queued: false
    property int starts: 0
    property int exits: 0
    property int stdoutCount: 0
    property int maxConcurrent: 0
    property int concurrent: 0
    property bool failed: false

    function request() {
        queued = true;
        if (!inFlight && !worker.running)
            Qt.callLater(startQueued);
    }

    function startQueued() {
        if (inFlight || worker.running || !queued) return;
        queued = false;
        inFlight = true;
        starts++;
        concurrent++;
        maxConcurrent = Math.max(maxConcurrent, concurrent);
        console.log("V0571 START run=" + starts);
        worker.running = true;
    }

    Process {
        id: worker
        running: false
        command: ["bash", "-c", "sleep 0.5; printf test-output"]
        stdout: StdioCollector {
            onStreamFinished: {
                root.stdoutCount++;
                console.log("V0571 STDOUT text=" + this.text);
            }
        }
        onExited: (code, status) => {
            root.exits++;
            root.concurrent--;
            root.inFlight = false;
            console.log("V0571 EXIT code=" + code + " status=" + status);
            if (root.queued)
                Qt.callLater(root.startQueued);
        }
    }

    Timer {
        interval: 100
        running: true
        repeat: false
        onTriggered: {
            for (let i = 0; i < 5; i++) root.request();
            console.log("V0571 FIVE_REQUESTS");
        }
    }

    Timer {
        interval: 250
        running: true
        repeat: false
        onTriggered: {
            for (let i = 0; i < 5; i++) root.request();
            console.log("V0571 FIVE_MORE_REQUESTS");
        }
    }

    Timer {
        interval: 3000
        running: true
        repeat: false
        onTriggered: {
            let ok = root.starts === 2 && root.exits === 2
                     && root.stdoutCount === 2 && root.maxConcurrent === 1
                     && !root.inFlight && !root.queued;
            console.log("V0571 RESULT starts=" + root.starts
                + " exits=" + root.exits
                + " stdout=" + root.stdoutCount
                + " maxConcurrent=" + root.maxConcurrent
                + " passed=" + ok);
            Qt.quit();
        }
    }
}
