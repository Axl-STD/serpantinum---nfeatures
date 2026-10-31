import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    property bool inFlight: false
    property bool queued: false
    property bool blocked: false
    property int starts: 0
    property int exits: 0
    property int timeouts: 0
    property int concurrent: 0
    property int maxConcurrent: 0
    property bool exitBeforeRetry: true
    property bool resultPassed: false

    function request() {
        if (blocked) return;
        queued = true;
        if (!inFlight && !worker.running)
            Qt.callLater(startQueued);
    }

    function startQueued() {
        if (blocked || inFlight || worker.running || !queued) return;
        if (starts > 0 && exits !== starts)
            exitBeforeRetry = false;
        queued = false;
        inFlight = true;
        starts++;
        concurrent++;
        maxConcurrent = Math.max(maxConcurrent, concurrent);
        console.log("V0571 START run=" + starts);
        worker.running = true;
        watchdog.start();
    }

    Process {
        id: worker
        running: false
        command: ["/usr/bin/sleep", "5"]
        stdout: StdioCollector {
            onStreamFinished: {
                console.log("V0571 STDOUT length=" + this.text.length);
            }
        }
        onExited: (code, status) => {
            watchdog.stop();
            cancelGrace.stop();
            root.exits++;
            root.concurrent--;
            root.inFlight = false;
            console.log("V0571 EXIT run=" + root.exits
                + " code=" + code + " status=" + status);
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
            root.timeouts++;
            console.log("V0571 TIMEOUT count=" + root.timeouts);
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
                console.log("V0571 BLOCKED unconfirmed-cancel");
            }
        }
    }

    Timer {
        interval: 100
        running: true
        repeat: false
        onTriggered: root.request()
    }

    Timer {
        interval: 2500
        running: true
        repeat: false
        onTriggered: {
            root.request();
            console.log("V0571 POST_BLOCK_REQUEST");
        }
    }

    Timer {
        interval: 3500
        running: true
        repeat: false
        onTriggered: {
            root.resultPassed = root.starts === 2
                && root.exits === 2
                && root.timeouts === 2
                && root.blocked
                && root.maxConcurrent === 1
                && root.exitBeforeRetry
                && !root.inFlight
                && !root.queued
                && !worker.running;
            console.log("V0571 RESULT starts=" + root.starts
                + " exits=" + root.exits
                + " timeouts=" + root.timeouts
                + " blocked=" + root.blocked
                + " maxConcurrent=" + root.maxConcurrent
                + " exitBeforeRetry=" + root.exitBeforeRetry
                + " passed=" + root.resultPassed);
            Qt.quit();
        }
    }
}
