#!/usr/bin/env python3
"""v0.5.75.2: source-bound Quickshell diagnostic lifecycle fixtures; no compositor/systemd."""
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
EXPECTED = '659a2302cd86a745b984817a82795b8b98adcc5c835696a19970c7d9047a39fc'


def block(text, marker, start=0):
    p = text.index(marker, start)
    opening = text.index('{', p + len(marker))
    depth, quote, line, multi = 1, None, False, False
    i = opening + 1
    while i < len(text):
        c, n = text[i], text[i+1:i+2]
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
            if depth == 0: return text[opening+1:i]
        i += 1
    raise ValueError('Unbalanced: ' + marker)


def fixture(source, scenario):
    diag = source.index('id: displayDiagnosticProcess')
    stdout = block(source, 'onStreamFinished:', diag)
    exited = block(source, 'onExited: (exitCode, exitStatus) =>', diag)
    wd = block(source, 'onTriggered:', source.index('id: diagnosticWatchdog'))
    grace = block(source, 'onTriggered:', source.index('id: diagnosticCancelGrace'))
    request = block(source, 'function requestDisplayDiagnostic()')
    if 'diagnosticTimedOut' not in stdout or 'diagnosticBlocked' not in exited:
        raise ValueError('Diagnostic lifecycle guards missing')
    valid = json.dumps({'assessment':'stable-observation','underlyingAssessment':'consistent-observable',
        'executionAuthorized':False,'finalizationAuthorized':False,'reservationReusable':False,
        'physicalExecutionEnabled':False,'physicalRollbackVerified':False,
        'atomicCrossStoreSnapshot':False,'commandsExecuted':0},separators=(',',':'))
    if scenario == 'success':
        command = ['/usr/bin/printf','%s',valid]
    else:
        command = ['/usr/bin/sleep','8']
    # In blocked scenario the cancellation-grace callback is deliberately injected while
    # the process is active, then the process is stopped. This tests fail-closed state,
    # not a real unkillable OS process.
    extra = ('Timer { interval: 500; running: true; repeat: false; onTriggered: { '
             'diagnosticCancelGrace.stop(); diagnosticBlocked = true; diagnosticTimedOut = true; '
             'diagnosticResponse = ""; diagnosticResponseValid = false; '
             'diagnosticAssessment = "observation-failed"; diagnosticMessageKey = "process_failed"; '
             'displayDiagnosticProcess.running = false; } }' if scenario == 'blocked-injected' else '')
    # Timeout accelerated in fixture only; source watchdog body is unchanged.
    watchdog_ms = 350 if scenario == 'timeout' else 4000
    return '''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
 id: displayTabRoot
 property string diagnosticRoot: "/tmp/fixture"
 property bool diagnosticBusy: false
 property bool diagnosticTimedOut: false
 property bool diagnosticBlocked: false
 property bool diagnosticResponseValid: false
 property string diagnosticResponse: ""
 property string diagnosticAssessment: "diagnostic-unavailable"
 property string diagnosticUnderlyingAssessment: ""
 property string diagnosticMessageKey: "not_requested"
 property int starts: 0
 property int exits: 0
 property int stdoutAccepted: 0
 property int timeoutEvents: 0
 property int graceEvents: 0
 Timer { id: diagnosticWatchdog; interval: WATCHDOG_MS; repeat: false; onTriggered: { timeoutEvents++; console.log("V05752TRACE WATCHDOG");
 WD_BODY
 } }
 Timer { id: diagnosticCancelGrace; interval: 2500; repeat: false; onTriggered: { graceEvents++; console.log("V05752TRACE GRACE");
 GRACE_BODY
 } }
 function requestDisplayDiagnostic() {
 REQUEST_BODY
 }
 Process {
  id: displayDiagnosticProcess
  running: false
  command: COMMAND
  onStarted: { displayTabRoot.starts++; console.log("V05752TRACE START"); }
  stdout: StdioCollector { onStreamFinished: {
   const wasValid = displayTabRoot.diagnosticResponseValid;
   STDOUT_BODY
   if (!wasValid && displayTabRoot.diagnosticResponseValid) displayTabRoot.stdoutAccepted++;
  } }
  onExited: (exitCode, exitStatus) => {
   displayTabRoot.exits++;
   console.log("V05752TRACE EXIT code="+exitCode+" status="+exitStatus);
   EXIT_BODY
  }
 }
 Timer {
  interval: 25
  repeat: true
  running: true
  onTriggered: {
   if (displayTabRoot.exits > 0) {
    stop();
    displayTabRoot.finish();
   }
  }
 }
 function finish() {
  const ok = SCENARIO === "success" ?
   (starts===1 && exits===1 && timeoutEvents===0 && diagnosticAssessment==="stable-observation" && !diagnosticBusy && !diagnosticBlocked) :
   SCENARIO === "timeout" ?
   (starts===1 && exits===1 && timeoutEvents===1 && diagnosticAssessment==="observation-failed" && !diagnosticBusy && diagnosticTimedOut && !diagnosticResponseValid) :
   (starts===1 && exits===1 && diagnosticBlocked && diagnosticAssessment==="observation-failed" && !diagnosticResponseValid);
  console.log("V05752 RESULT scenario="+SCENARIO+" starts="+starts+" exits="+exits+
    " timeout="+timeoutEvents+" blocked="+diagnosticBlocked+" passed="+ok);
  Qt.quit();
 }
 Timer { interval: 50; running: true; repeat: false; onTriggered: requestDisplayDiagnostic() }
 EXTRA
 Timer { interval: 7000; running: true; repeat: false; onTriggered: { console.log("V05752TRACE FINAL busy="+diagnosticBusy+" blocked="+diagnosticBlocked+" timedOut="+diagnosticTimedOut+" running="+displayDiagnosticProcess.running+" starts="+starts+" exits="+exits+" watchdog="+timeoutEvents+" grace="+graceEvents); console.log("V05752 FAIL fixture timeout"); Qt.quit(); } }
}
'''.replace('WATCHDOG_MS',str(watchdog_ms)).replace('WD_BODY',wd).replace('GRACE_BODY',grace).replace('REQUEST_BODY',request).replace('STDOUT_BODY',stdout).replace('EXIT_BODY',exited).replace('COMMAND',json.dumps(command)).replace('SCENARIO',json.dumps(scenario)).replace('EXTRA',extra)


def main():
    if sys.argv[1:] != ['selftest']:
        print('Usage: display-diagnostic-runtime-v05752.py selftest', file=sys.stderr)
        return 2
    source = SOURCE.read_bytes()
    if hashlib.sha256(source).hexdigest() != EXPECTED:
        print('ERROR: Source QML differs from audited base',file=sys.stderr)
        return 2
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05752-') as tmp:
        work = Path(tmp)
        os.chmod(work,0o700)
        for scenario in ('success','timeout','blocked-injected'):
            qml = work / (scenario+'.qml')
            qml.write_text(fixture(source.decode('utf-8'),scenario))
            try:
                proc = subprocess.run(['quickshell','-n','-p',str(qml)],cwd=work,
                    env={**os.environ,'QS_CACHE_DIR':str(work/'cache'),'QS_STATE_DIR':str(work/'state')},
                    capture_output=True,text=True,timeout=10)
                out = proc.stdout+'\n'+proc.stderr
                markers = re.findall(r'V05752 RESULT scenario='+re.escape(scenario)+r' .*?passed=(true|false)',out)
                passed = proc.returncode==0 and markers==['true'] and 'V05752 FAIL' not in out
                results.append({'scenario':scenario,'passed':passed,'exitCode':proc.returncode,
                                'resultMarkers':markers,'logTail':'' if passed else out[-1500:]})
            except subprocess.TimeoutExpired:
                results.append({'scenario':scenario,'passed':False,'error':'Quickshell timeout'})
    success = all(r['passed'] for r in results) and len(results)==3
    print(json.dumps({'version':'0.5.75.2','mode':'source-bound-quickshell-diagnostic-runtime',
        'selftest':'passed' if success else 'failed','totalScenarios':len(results),'results':results,
        'physicalConfigurationCommandsExecuted':0,
        'limitations':['Minimal Quickshell fixture, not full Settings runtime',
                       'blocked-injected simulates unconfirmed cancellation, not an unkillable process',
                       'Fixture accelerates timeout to 350ms; production remains 4000ms']},indent=2))
    return 0 if success else 2

if __name__ == '__main__':
    sys.exit(main())
