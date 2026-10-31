#!/usr/bin/env python3
"""v0.5.73: source-bound Quickshell runtime fixture; no compositor commands."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'guide/display/DisplayMainState.qml'
EXPECTED = 'ce7c41defcc95b7b7980913b1c46e6223b90266f1d3a385c78c03f7881d99305'

def block(source, marker, start=0):
    p = source.index(marker, start)
    opening = source.index('{', p + len(marker))
    depth = 1
    quote = None
    line = False
    multi = False
    i = opening + 1
    while i < len(source):
        c = source[i]
        n = source[i+1:i+2]
        if line:
            if c == '\n': line = False
        elif multi:
            if c == '*' and n == '/': multi = False; i += 1
        elif quote:
            if c == '\\': i += 1
            elif c == quote: quote = None
        elif c == '/' and n == '/': line = True; i += 1
        elif c == '/' and n == '*': multi = True; i += 1
        elif c in ('"', "'", '`'): quote = c
        elif c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return source[opening+1:i]
        i += 1
    raise ValueError('Unbalanced ' + marker)

def main():
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        print('Usage: display-runtime-production-v0573.py selftest', file=sys.stderr)
        return 2
    source = SOURCE.read_text()
    if hashlib.sha256(source.encode()).hexdigest() != EXPECTED:
        print('ERROR: Production QML hash differs from audited base', file=sys.stderr)
        return 2
    pos = source.index('id: monitorDetector')
    stdout = block(source, 'onStreamFinished:', pos)
    exited = block(source, 'onExited: (exitCode, exitStatus) =>', pos)
    assert 'monitorDetectionCandidate = mList' in stdout
    assert 'monitorsList = candidate' in exited
    scenarios = [
        ('valid', 'printf', 0, False, False, 'NEW', 'updated'),
        ('invalid', 'printf', 0, False, False, 'OLD', 'failed'),
        ('nonzero', 'printf', 7, False, False, 'OLD', 'failed'),
        ('queued', 'printf', 0, True, False, 'OLD', 'checking'),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0573-runtime-') as temp:
        work = Path(temp)
        os.chmod(work, 0o700)
        for name, _, code, queued, blocked, expected_name, expected_status in scenarios:
            payload = '[{"name":"NEW","width":1920,"height":1080,"scale":1,"refreshRate":60}]' if name != 'invalid' else '{bad'
            # Run a fixed, innocuous binary with literal data, no shell or compositor.
            command = json.dumps(['/usr/bin/printf', '%s', payload]) if code == 0 else json.dumps(['/usr/bin/false'])
            qml = '''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
 id: displayTabRoot
 property string compositor: "hyprland"
 property bool monitorDetectionInFlight: true
 property bool monitorDetectionQueued: QUEUED
 property bool monitorDetectionTimedOut: false
 property bool monitorDetectionBlocked: BLOCKED
 property int monitorDetectionTimeouts: 0
 property bool monitorDetectionReceivedValidOutput: false
 property var monitorDetectionCandidate: null
 property string monitorDetectionStatus: "checking"
 property var monitorsList: [{name:"OLD",active:true}]
 property bool monitorTopologyVerifiedOnce: false
 property string pendingMonScaleName: ""
 property string selectedMonitorName: "OLD"
 property int published: 0
 property int ageRecorded: 0
 property int exits: 0
 property int stdoutEvents: 0
 property int starts: 0
 function reconcilePendingDisplayChanges(candidate) { published++; }
 function ensureDisplaySelection() {}
 function refreshDisplaySettings() {}
 function recordSuccessfulMonitorDetection() { ageRecorded++; }
 function startQueuedMonitorDetection() { monitorDetectionQueued = false; }
 QtObject { id: monitorDetectionWatchdog; function stop() {} }
 QtObject { id: monitorDetectionCancelGrace; function stop() {} }
 QtObject { id: scaleDebounceTimer; function stop() {} }
 Process {
  id: monitorDetector
  running: false
  command: COMMAND
  onStarted: displayTabRoot.starts++
  stdout: StdioCollector {
    onStreamFinished: {
      displayTabRoot.stdoutEvents++;
STDOUT_BODY
    }
  }
  onExited: (exitCode, exitStatus) => {
    displayTabRoot.exits++;
EXIT_BODY
    Qt.callLater(checkResult);
  }
 }
 function checkResult() {
   const ok = exits === 1 && starts === 1 &&
       monitorsList[0].name === "EXPECTED_NAME" &&
       monitorDetectionStatus === "EXPECTED_STATUS" &&
       published === EXPECTED_PUBLISHED &&
       ageRecorded === EXPECTED_PUBLISHED &&
       monitorDetectionCandidate === null &&
       monitorDetectionReceivedValidOutput === false;
   console.log("V0573 RESULT scenario=NAME starts="+starts+" exits="+exits+
      " stdout="+stdoutEvents+" published="+published+
      " status="+monitorDetectionStatus+" passed="+ok);
   Qt.quit();
 }
 Timer { interval: 50; running: true; repeat: false; onTriggered: monitorDetector.running = true }
 Timer { interval: 2000; running: true; repeat: false; onTriggered: { console.log("V0573 FAIL timeout"); Qt.quit(); } }
}
'''
            replacements = {
                'QUEUED': 'true' if queued else 'false', 'BLOCKED': 'true' if blocked else 'false',
                'COMMAND': command, 'STDOUT_BODY': stdout, 'EXIT_BODY': exited,
                'EXPECTED_NAME': expected_name, 'EXPECTED_STATUS': expected_status,
                'EXPECTED_PUBLISHED': '1' if expected_name == 'NEW' else '0', 'NAME': name
            }
            for key, value in replacements.items():
                qml = qml.replace(key, value)
            qmlfile = work / (name + '.qml')
            qmlfile.write_text(qml)
            try:
                proc = subprocess.run(['quickshell', '-n', '-p', str(qmlfile)], cwd=work, capture_output=True,
                    text=True, timeout=5, env={**os.environ, 'QS_CACHE_DIR':str(work/'cache'), 'QS_STATE_DIR':str(work/'state')})
                output = proc.stdout + '\n' + proc.stderr
                matched = re.findall(r'V0573 RESULT scenario=' + name + r' .*?passed=(true|false)', output)
                passed = proc.returncode == 0 and matched == ['true'] and 'V0573 FAIL' not in output
                results.append({'scenario':name,'passed':passed,'exitCode':proc.returncode,'resultMarkers':matched,
                                'logTail': '' if passed else output[-1000:]})
            except subprocess.TimeoutExpired:
                results.append({'scenario':name,'passed':False,'error':'Quickshell timeout'})
    success = len(results) == len(scenarios) and all(x['passed'] for x in results)
    print(json.dumps({'version':'0.5.73','mode':'source-bound-quickshell-runtime','selftest':'passed' if success else 'failed',
        'totalScenarios':len(results),'results':results,'physicalConfigurationCommandsExecuted':0,
        'limitations':['Production callback bodies embedded in a minimal fixture, not the full Settings component',
                       'No compositor query or physical rollback proof']}, indent=2))
    return 0 if success else 2

if __name__ == '__main__':
    sys.exit(main())
