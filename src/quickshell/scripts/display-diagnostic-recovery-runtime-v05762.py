#!/usr/bin/env python3
"""v0.5.76.2: source-bound Quickshell recovery fixture. No compositor/systemd calls."""
import hashlib
import importlib.util
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
PREVIOUS = Path(__file__).with_name('display-diagnostic-runtime-v05752.py')
SCENARIOS = ('blocked-before-exit', 'blocked-after-exit', 'manual-recovery', 'explicit-retry', 'late-output')


def fixture(source, scenario, block):
    diag = source.index('id: displayDiagnosticProcess')
    stdout = block(source, 'onStreamFinished:', diag)
    exited = block(source, 'onExited: (exitCode, exitStatus) =>', diag)
    request = block(source, 'function requestDisplayDiagnostic()')
    recover = block(source, 'function recoverDisplayDiagnostic()')
    wd = block(source, 'onTriggered:', source.index('id: diagnosticWatchdog'))
    grace = block(source, 'onTriggered:', source.index('id: diagnosticCancelGrace'))
    for fragment in ('diagnosticExitConfirmed = true', 'diagnosticBusy = false'):
        if fragment not in exited:
            raise ValueError('Missing production exit confirmation: ' + fragment)
    if 'if (!diagnosticRecoveryAvailable) return;' not in recover:
        raise ValueError('Recovery safety gate missing')
    if 'diagnosticBlocked' not in stdout or 'diagnosticTimedOut' not in stdout:
        raise ValueError('Late stdout guard missing')
    valid = json.dumps({'assessment': 'stable-observation', 'underlyingAssessment': 'consistent-observable',
                        'executionAuthorized': False, 'finalizationAuthorized': False,
                        'reservationReusable': False, 'physicalExecutionEnabled': False,
                        'physicalRollbackVerified': False, 'atomicCrossStoreSnapshot': False,
                        'commandsExecuted': 0}, separators=(',', ':'))
    qml = r'''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
 id: displayTabRoot
 property string diagnosticRoot: "/tmp/fixture"
 property bool diagnosticBusy: false
 property bool diagnosticTimedOut: false
 property bool diagnosticBlocked: false
 property bool diagnosticExitConfirmed: false
 readonly property bool diagnosticRecoveryAvailable: diagnosticBlocked && diagnosticExitConfirmed && !diagnosticBusy && !displayDiagnosticProcess.running
 property bool diagnosticResponseValid: false
 property string diagnosticResponse: ""
 property string diagnosticAssessment: "diagnostic-unavailable"
 property string diagnosticUnderlyingAssessment: ""
 property string diagnosticMessageKey: "not_requested"
 property int starts: 0
 property int exits: 0
 property bool beforeExitRejected: false
 property bool afterExitAvailable: false
 property bool recoveryClean: false
 property bool lateRejected: false
 property bool retryPassed: false
 property bool finished: false
 property string scenario: SCENARIO
 property string validOutput: VALID
 Timer { id: diagnosticWatchdog; interval: 4000; repeat: false; onTriggered: { WD_BODY } }
 Timer { id: diagnosticCancelGrace; interval: 2500; repeat: false; onTriggered: { GRACE_BODY } }
 function requestDisplayDiagnostic() { REQUEST_BODY }
 function recoverDisplayDiagnostic() { RECOVER_BODY }
 QtObject {
  id: lateCollector
  property string text: "STALE-UNTRUSTED-OUTPUT"
  function simulate() { STDOUT_BODY }
 }
 Process {
  id: displayDiagnosticProcess
  running: false
  command: ["/usr/bin/sleep", "8"]
  onStarted: { displayTabRoot.starts++; console.log("V05762TRACE START=" + starts); }
  stdout: StdioCollector { onStreamFinished: { STDOUT_BODY } }
  onExited: (exitCode, exitStatus) => {
   displayTabRoot.exits++;
   console.log("V05762TRACE EXIT code="+exitCode+" status="+exitStatus);
   EXIT_BODY
  }
 }
 function result(ok) {
  if (finished) return;
  finished = true;
  console.log("V05762 RESULT scenario="+scenario+" starts="+starts+" exits="+exits+
   " blocked="+diagnosticBlocked+" confirmed="+diagnosticExitConfirmed+
   " recoveryAvailable="+diagnosticRecoveryAvailable+" passed="+ok);
  Qt.quit();
 }
 Timer { interval: 50; running: true; repeat: false; onTriggered: requestDisplayDiagnostic() }
 // Simulate expiration of the cancellation grace while the harmless sleep is still active.
 Timer { interval: 170; running: true; repeat: false; onTriggered: {
  diagnosticTimedOut = true;
  diagnosticCancelGrace.stop();
  diagnosticBlocked = true;
  diagnosticResponse = "";
  diagnosticResponseValid = false;
  diagnosticAssessment = "observation-failed";
  diagnosticMessageKey = "process_failed";
 } }
 Timer { interval: 240; running: true; repeat: false; onTriggered: {
  const original = starts;
  recoverDisplayDiagnostic();
  requestDisplayDiagnostic();
  beforeExitRejected = diagnosticBlocked && !diagnosticExitConfirmed &&
   !diagnosticRecoveryAvailable && diagnosticBusy && starts === original;
  if (scenario === "blocked-before-exit") result(beforeExitRejected);
 } }
 Timer { interval: 330; running: true; repeat: false; onTriggered: {
  if (scenario !== "blocked-before-exit") displayDiagnosticProcess.running = false;
 } }
 Timer { interval: 25; repeat: true; running: true; onTriggered: {
  if (scenario === "blocked-before-exit" || exits !== 1 || diagnosticBusy) return;
  stop();
  afterExitAvailable = diagnosticBlocked && diagnosticExitConfirmed &&
   !displayDiagnosticProcess.running && diagnosticRecoveryAvailable;
  if (scenario === "blocked-after-exit") { result(beforeExitRejected && afterExitAvailable); return; }
  if (scenario === "late-output") {
   lateCollector.simulate();
   lateRejected = !diagnosticResponseValid && diagnosticResponse === "" && diagnosticBlocked;
   result(beforeExitRejected && afterExitAvailable && lateRejected);
   return;
  }
  const originalStarts = starts;
  recoverDisplayDiagnostic();
  recoveryClean = !diagnosticBlocked && !diagnosticExitConfirmed &&
   !diagnosticTimedOut && !diagnosticResponseValid && diagnosticResponse === "" &&
   diagnosticAssessment === "diagnostic-unavailable" && starts === originalStarts &&
   !displayDiagnosticProcess.running;
  if (scenario === "manual-recovery") { result(beforeExitRejected && afterExitAvailable && recoveryClean); return; }
  // Only an explicit request may start the second, harmless process.
  displayDiagnosticProcess.command = ["/usr/bin/printf", "%s", validOutput];
  Qt.callLater(function() { requestDisplayDiagnostic(); });
 } }
 Timer { interval: 25; repeat: true; running: true; onTriggered: {
  if (scenario !== "explicit-retry" || exits !== 2 || diagnosticBusy || finished) return;
  stop();
  retryPassed = starts === 2 && !diagnosticBlocked && diagnosticExitConfirmed &&
   diagnosticAssessment === "stable-observation" && diagnosticResponseValid;
  result(beforeExitRejected && afterExitAvailable && recoveryClean && retryPassed);
 } }
 Timer { interval: 5500; repeat: false; running: true; onTriggered: {
  console.log("V05762 FAIL fixture deadline starts="+starts+" exits="+exits+
   " busy="+diagnosticBusy+" blocked="+diagnosticBlocked);
  Qt.quit();
 } }
}
'''
    replacements = {'SCENARIO': json.dumps(scenario), 'VALID': json.dumps(valid),
                    'WD_BODY': wd, 'GRACE_BODY': grace, 'REQUEST_BODY': request,
                    'RECOVER_BODY': recover, 'STDOUT_BODY': stdout, 'EXIT_BODY': exited}
    for key, value in replacements.items():
        qml = qml.replace(key, value)
    return qml


def main():
    if sys.argv[1:] != ['selftest']:
        print('Usage: display-diagnostic-recovery-runtime-v05762.py selftest', file=sys.stderr)
        return 2
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED:
        print('ERROR: Production QML differs from audited source', file=sys.stderr)
        return 2
    spec = importlib.util.spec_from_file_location('display_v05752', PREVIOUS)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    source = SOURCE.read_text(encoding='utf-8')
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05762-') as tmp:
        work = Path(tmp)
        os.chmod(work, 0o700)
        for scenario in SCENARIOS:
            qml = work / (scenario + '.qml')
            qml.write_text(fixture(source, scenario, previous.block), encoding='utf-8')
            try:
                env = {**os.environ, 'QS_CACHE_DIR': str(work / 'cache'),
                       'QS_STATE_DIR': str(work / 'state'), 'QS_RUN_DIR': str(work / 'run')}
                proc = subprocess.run(['quickshell', '-n', '-p', str(qml)], cwd=work,
                                      env=env, capture_output=True, text=True, timeout=8)
                output = proc.stdout + '\n' + proc.stderr
                markers = re.findall(r'V05762 RESULT scenario=' + re.escape(scenario) +
                                     r' .*?passed=(true|false)', output)
                passed = proc.returncode == 0 and markers == ['true'] and 'V05762 FAIL' not in output
                results.append({'scenario': scenario, 'passed': passed, 'exitCode': proc.returncode,
                                'resultMarkers': markers, 'logTail': '' if passed else output[-2400:]})
            except subprocess.TimeoutExpired:
                results.append({'scenario': scenario, 'passed': False, 'error': 'Quickshell timeout'})
            except OSError as exc:
                results.append({'scenario': scenario, 'passed': False, 'error': str(exc)})
    passed = len(results) == len(SCENARIOS) and all(r['passed'] for r in results)
    print(json.dumps({'version': '0.5.76.2', 'mode': 'source-bound-quickshell-recovery-runtime',
                      'selftest': 'passed' if passed else 'failed', 'totalScenarios': len(results),
                      'results': results, 'physicalConfigurationCommandsExecuted': 0,
                      'limitations': ['Minimal Quickshell fixture, not the full Settings component',
                                      'Blocked state is injected while a supervised sleep process runs',
                                      'Does not prove an unkillable process or physical rollback']}, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    sys.exit(main())
