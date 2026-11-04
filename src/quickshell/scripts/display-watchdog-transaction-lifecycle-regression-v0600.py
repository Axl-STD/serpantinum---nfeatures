#!/usr/bin/env python3
"""Offline transaction lifecycle regression; never schedules systemd or changes displays."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('txn_lifecycle', HERE/'display-watchdog-transaction-lifecycle-v0600.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
rows = []
def check(name, value):
    rows.append(dict(scenario=name, passed=bool(value)))
    if not value:
        raise AssertionError(name)

def denied(name, call):
    try:
        call()
    except (m.controller.RecoveryDenied, ValueError):
        check(name, True)
    else:
        check(name, False)

with tempfile.TemporaryDirectory(prefix='serpantinum-txn-v0600-') as td:
    root = Path(td)
    root.chmod(0o700)
    check('no-state', m.run_watchdog(root)['decision']=='BLOCK_NO_RECOVERY_STATE')
    record = m.prepare_armed(root, 2)
    check('armed', record['state']=='ARMED')
    check('generation-two', record['generation']==2)
    check('snapshot', record['snapshot']==m.snapshot())
    check('before-deadline', m.run_watchdog(root)['state']=='ARMED')
    denied('cannot-prepare-twice', lambda:m.prepare_armed(root,2))
    denied('cannot-apply', lambda:m.controller.transition(root,record['id'],2,'APPLIED'))
    denied('cannot-confirm-armed', lambda:m.controller.transition(root,record['id'],2,'CONFIRMED'))
    check('armed-still',m.controller.inspect(root)['state']=='ARMED')
    check('forced-expire',m.watchdog.decision(root,record['deadline_ns'])['state']=='ROLLBACK_REQUIRED')
    check('generation-three',m.controller.inspect(root)['generation']==3)
    check('deadline-idempotent',m.watchdog.decision(root,record['deadline_ns']+1)['generation']==3)
    check('snapshot-preserved',m.controller.inspect(root)['snapshot']==m.snapshot())
    denied('cannot-fake-rollback',lambda:m.controller.transition(root,record['id'],3,'ROLLED_BACK'))
    check('requires-rollback',m.controller.inspect(root)['state']=='ROLLBACK_REQUIRED')
    check('no-execution',m.run_watchdog(root)['commandsExecuted']==0)
    check('no-physical',m.run_watchdog(root)['physicalExecutionEnabled'] is False)
    denied('invalid-delay-zero',lambda:m.prepare_armed(root,0))
    denied('invalid-delay-large',lambda:m.prepare_armed(root,121))
    denied('invalid-delay-bool',lambda:m.prepare_armed(root,True))
    denied('invalid-unit',lambda:m.launch_systemd(root,'other-unit'))
    denied('invalid-unit-special',lambda:m.launch_systemd(root,'serpantinum-v0600-txn-;'))
    denied('invalid-systemd-delay',lambda:m.launch_systemd(root,'serpantinum-v0600-txn-test','999s'))
    result=subprocess.run([sys.executable,'-B',str(HERE/'display-watchdog-transaction-lifecycle-v0600.py'),'inspect',str(root)],capture_output=True,text=True,timeout=10)
    check('cli-inspect-exit',result.returncode==0)
    check('cli-inspect-state',json.loads(result.stdout)['state']=='ROLLBACK_REQUIRED')
    result=subprocess.run([sys.executable,'-B',str(HERE/'display-watchdog-transaction-lifecycle-v0600.py'),'expire',str(root)],capture_output=True,text=True,timeout=10)
    check('cli-expire-exit',result.returncode==0)
    check('cli-expire-no-physical',json.loads(result.stdout)['commandsExecuted']==0)
with tempfile.TemporaryDirectory(prefix='serpantinum-txn-v0600-') as td:
    root=Path(td);root.chmod(0o700)
    record=m.prepare_armed(root,2)
    check('second-armed',record['state']=='ARMED')
    result=m.watchdog.decision(root,record['deadline_ns']-1)
    check('second-predeadline',result['state']=='ARMED')
    result=m.watchdog.decision(root,record['deadline_ns'])
    check('second-expired',result['state']=='ROLLBACK_REQUIRED')
    check('second-generation',m.controller.inspect(root)['generation']==3)
with tempfile.TemporaryDirectory(prefix='serpantinum-txn-v0600-') as td:
    root=Path(td);root.chmod(0o700)
    (root/'recovery.json').write_text('bad')
    check('corrupt-blocks',m.run_watchdog(root)['decision']=='BLOCK_AND_INVESTIGATE')
    check('corrupt-no-command',m.run_watchdog(root)['commandsExecuted']==0)
    denied('corrupt-cannot-prepare',lambda:m.prepare_armed(root,2))
payload=dict(version='0.6.0',mode='offline-watchdog-transaction-lifecycle',selftest='passed',totalScenarios=len(rows),results=rows,executionAuthorized=False,physicalExecutionEnabled=False,commandsExecuted=0,limitations=['No APPLIED/CONFIRMED physical workflow','No physical rollback','Systemd schedule entrypoint is opt-in and not invoked by regression','Wall clock deadlines are not monotonic'])
print(json.dumps(payload,indent=2))
