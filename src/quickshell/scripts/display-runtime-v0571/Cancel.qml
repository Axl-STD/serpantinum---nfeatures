import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    property bool inFlight: false
    property bool queued: false
    property bool timedOut: false
    property bool blocked: false
    property int starts: 0
    property int exits: 0
    property int timeouts: 0
    property int maxConcurrent: 0
    property int concurrent: 0
    property bool retrySuccess: false

    function startQueued() {
        if (blocked || inFlight || worker.running || !queued) return;
        queued = false;
        inFlight = true;
        timedOut = false;
        starts++;
        concurrent++;
        maxConcurrent = Math.max(maxConcurrent, concurrent);
        console.log("V0571 START run=" + starts);
        worker.command = starts === 1
            ? ["bash", "-c", "sleep 5"]
            : ["bash", "-c", "printf recovered"];
        worker.running = true;
        watchdog.start();
    }

    Process {
        id: worker
        running: false
        command: ["bash", "-c", "sleep 5"]
        stdout: StdioCollector {
            onStreamFinished: {
                console.log("V0571 STDOUT text=" + this.text);
            }
        }
        onExited: (code, status) => {
            watchdog.stop();
            cancelGrace.stop();
            root.exits++;
            root.concurrent--;
            root.inFlight = false;
            console.log("V0571 EXIT run=" + root.starts
                + " code=" + code + " status=" + status);
            if (root.starts === 2 && code === 0 && status === 0)
                root.retrySuccess = true;
            if (!root.blocked && root.queued)
                Qt.callLater(root.startQueued);
        }
    }

    Timer {
        id: watchdog
        interval: 500
        repeat: false
        onTriggered: {
            if (!root.inFlight || root.blocked) return;
            root.timedOut = true;
            root.timeouts++;
            console.log("V0571 TIMEOUT run=" + root.starts);
            if (root.timeouts === 1)
                root.queued = true;
            else {
                root.blocked = true;
                root.queued = false;
            }
            cancelGrace.start();
            worker.running = false;
        }
    }

    Timer {
        id: cancelGrace
        interval: 1500
        repeat: false
        onTriggered: {
            if (root.inFlight) {
                root.blocked = true;
                root.queued = false;
                console.log("V0571 BLOCKED cancellation-unconfirmed");
            }
        }
    }

    Timer {
        interval: 100
        running: true
        repeat: false
        onTriggered: {
            root.queued = true;
            root.startQueued();
        }
    }

    Timer {
        interval: 3500
        running: true
        repeat: false
        onTriggered: {
            const ok = root.starts === 2 && root.exits === 2
                && root.timeouts === 1 && root.maxConcurrent === 1
                && root.retrySuccess && !root.blocked
                && !root.inFlight && !root.queued;
            console.log("V0571 RESULT starts=" + root.starts
                + " exits=" + root.exits
                + " timeouts=" + root.timeouts
                + " maxConcurrent=" + root.maxConcurrent
                + " blocked=" + root.blocked
                + " retrySuccess=" + root.retrySuccess
                + " passed=" + ok);
            Qt.quit();
        }
    }
}
