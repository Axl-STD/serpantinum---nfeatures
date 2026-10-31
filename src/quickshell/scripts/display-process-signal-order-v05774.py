#!/usr/bin/env python3
"""v0.5.77.4: Quickshell 0.3.2 Process signal ordering, harmless fixtures only.

This observes a single reused Process object; it does not execute production
handlers and cannot establish an absolute guarantee about all possible races.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'guide/display/DisplayMainState.qml'
EXPECTED = 'ce7c41defcc95b7b7980913b1c46e6223b90266f1d3a385c78c03f7881d99305'
SCENARIOS = ('normal-restart', 'cancel-restart', 'rapid-requests')

QML = r'''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
 id: root
 property string scenario: SCENARIO
 property int starts: 0
 property int exits: 0
 property int outputs: 0
 property int maxConcurrent: 0
 property int active: 0
 property int currentRun: 0
 property int duplicateExits: 0
 property int staleOutput: 0
 property int staleExit: 0
 property bool finished: false
 property bool cancellationRequested: false
 property var trace: []
 function log(event, detail) {
  const s = "V05774TRACE " + event + " run=" + currentRun + " " + detail;
  trace.push(s); console.log(s);
 }
 function commandFor(n) {
  const script = n === 1 && scenario === "cancel-restart"
    ? "import time;time.sleep(8);print('RUN1',flush=True)"
    : "import time;time.sleep(0.12);print('RUN"+n+"',flush=True)";
  return ["/usr/bin/python3", "-c", script];
 }
 function startRun() {
  if (process.running || active !== 0 || currentRun >= 2) return;
  currentRun++;
  process.command = commandFor(currentRun);
  process.running = true;
  log("REQUEST", "running="+process.running);
 }
 function result() {
  if (finished) return;
  finished = true;
  const ok = starts === 2 && exits === 2 && outputs === (scenario === "cancel-restart" ? 1 : 2)
   && maxConcurrent === 1 && active === 0 && duplicateExits === 0 && staleOutput === 0 && staleExit === 0
   && (scenario !== "cancel-restart" || cancellationRequested);
  console.log("V05774 RESULT scenario="+scenario+" starts="+starts+" exits="+exits+
   " outputs="+outputs+" maxConcurrent="+maxConcurrent+" staleOutput="+staleOutput+
   " staleExit="+staleExit+" duplicateExits="+duplicateExits+" passed="+ok);
  Qt.quit();
 }
 Process {
  id: process
  running: false
  stdout: StdioCollector {
   onStreamFinished: {
    const content = this.text.trim();
    root.log("STDOUT", "value="+JSON.stringify(content));
    if (content.length > 0) {
     root.outputs++;
     if (content !== "RUN"+root.currentRun) root.staleOutput++;
    }
   }
  }
  onStarted: {
   root.starts++; root.active++;
   root.maxConcurrent = Math.max(root.maxConcurrent, root.active);
   root.log("START", "running="+running);
  }
  onRunningChanged: root.log("RUNNING_CHANGED", "running="+running)
  onExited: (code, status) => {
   root.log("EXIT", "code="+code+" status="+status+" running="+running);
   root.exits++;
   if (root.active === 0) root.duplicateExits++;
   else root.active--;
   if (root.currentRun === 1) {
    if (root.scenario === "cancel-restart" && !root.cancellationRequested) root.staleExit++;
    Qt.callLater(function() { root.startRun(); });
   } else {
    Qt.callLater(root.result);
   }
  }
 }
 Timer { interval: 80; repeat: false; running: true; onTriggered: root.startRun() }
 Timer { interval: 180; repeat: false; running: scenario === "cancel-restart"; onTriggered: {
   root.cancellationRequested = true;
   root.log("CANCEL_REQUEST", "running="+process.running);
   process.running = false;
 } }
 Timer { interval: 110; repeat: false; running: scenario === "rapid-requests"; onTriggered: {
   for (let i=0;i<8;i++) root.startRun();
   root.log("RAPID_REQUESTS", "starts="+root.starts);
 } }
 Timer { interval: 5000; repeat: false; running: true; onTriggered: {
   root.log("DEADLINE", "starts="+starts+" exits="+exits);
   console.log("V05774 FAIL deadline"); Qt.quit();
 } }
}
'''


def main():
    if sys.argv[1:] != ['selftest']:
        print('Usage: display-process-signal-order-v05774.py selftest', file=sys.stderr)
        return 2
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED:
        print('ERROR: QML differs from audited f899c8b source', file=sys.stderr)
        return 2
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05774-') as tmp:
        work = Path(tmp)
        os.chmod(work, 0o700)
        for scenario in SCENARIOS:
            qml = work / (scenario + '.qml')
            qml.write_text(QML.replace('SCENARIO', json.dumps(scenario)), encoding='utf-8')
            env = {**os.environ, 'QS_CACHE_DIR': str(work / ('cache-' + scenario)),
                   'QS_STATE_DIR': str(work / ('state-' + scenario)),
                   'QS_RUN_DIR': str(work / ('run-' + scenario))}
            try:
                proc = subprocess.run(['quickshell', '-n', '-p', str(qml)], cwd=work,
                                      env=env, capture_output=True, text=True, timeout=7)
                output = proc.stdout + '\n' + proc.stderr
                matches = re.findall(r'V05774 RESULT scenario=' + re.escape(scenario) +
                                     r' .*?passed=(true|false)', output)
                passed = proc.returncode == 0 and matches == ['true'] and 'V05774 FAIL' not in output
                results.append({'scenario': scenario, 'passed': passed, 'exitCode': proc.returncode,
                                'resultMarkers': matches, 'trace': re.findall(r'V05774TRACE [^\r\n]*', output),
                                'logTail': '' if passed else output[-2500:]})
            except subprocess.TimeoutExpired:
                results.append({'scenario': scenario, 'passed': False, 'error': 'Quickshell timed out'})
            except OSError as exc:
                results.append({'scenario': scenario, 'passed': False, 'error': str(exc)})
    passed = all(x['passed'] for x in results) and len(results) == len(SCENARIOS)
    print(json.dumps({'version': '0.5.77.4', 'mode': 'real-quickshell-single-process-signal-order',
                      'selftest': 'passed' if passed else 'failed', 'totalScenarios': len(results),
                      'results': results, 'physicalConfigurationCommandsExecuted': 0,
                      'productionModified': False, 'limitations': [
                          'Exercises Quickshell Process signals, not production diagnostic callback bodies',
                          'Absence of stale signals in these runs does not prove they are impossible',
                          'No compositor, systemd, physical configuration, or rollback testing']}, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    sys.exit(main())
