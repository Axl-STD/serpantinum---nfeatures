#!/usr/bin/env python3
"""Offline-only IPC autonomous worker. No physical display operations."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

recovery = load('display-unified-ipc-recovery-v0600.py', 'unified_auto_recovery')
ipc = recovery.ipc
SUCCESS = {'SIMULATED_ROLLBACK_VERIFIED', 'SIMULATED_RECONCILIATION_VERIFIED', 'TERMINAL_VERIFIED'}

def outcome(decision, detail=None):
    return {'version':'0.6.0','mode':'unified-ipc-autonomous-offline','decision':decision,
            'detail':detail,'physicalExecutionEnabled':False,'executionAuthorized':False,
            'commandsExecuted':0,'rollbackPhysicalVerified':False}

def verify(root, token, boot):
    with ipc.locked(root) as p:
        r = ipc.read(p, 'transaction.json')
        effect = ipc.read(p, 'simulated-ipc.json')
        if r.get('execution_backend', 'simulated') != 'simulated':
            return False
        return (r.get('id') == token and r.get('boot_id') == boot and
                r.get('state') == 'ROLLED_BACK' and r.get('phase') == 'COMPLETED' and
                effect == {'connector':r.get('target'),'mode':ipc.BASE})

def execute(root, max_wait=0, poll=0.1, sleeper=time.sleep):
    if type(max_wait) not in (int,float) or not 0 <= max_wait <= 120:
        raise ValueError('invalid wait')
    if type(poll) not in (int,float) or not 0.02 <= poll <= 2:
        raise ValueError('invalid poll')
    root = ipc.root_check(Path(root))
    with ipc.locked(root) as p:
        record = ipc.read(p, 'transaction.json')
        if record.get('execution_backend', 'simulated') != 'simulated':
            return outcome('BLOCK_PHYSICAL_TRANSACTION')
        token, boot = record['id'], record['boot_id']
    deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + max_wait
    while True:
        with ipc.locked(root) as p:
            current = ipc.read(p, 'transaction.json')
            if current.get('execution_backend', 'simulated') != 'simulated':
                return outcome('BLOCK_PHYSICAL_TRANSACTION')
            if current.get('id') != token or current.get('boot_id') != boot:
                return outcome('BLOCK_IDENTITY_CHANGED')
        result = recovery.execute(root)
        decision = result['decision']
        if decision in SUCCESS:
            return outcome('SIMULATED_RECOVERY_VERIFIED' if verify(root,token,boot)
                           else 'BLOCK_FINAL_VERIFICATION',decision)
        if decision == 'CONFIRMED_NO_ACTION':
            return outcome('CONFIRMED_NO_ACTION',decision)
        if decision != 'WAITING':
            return outcome('BLOCK_AND_INVESTIGATE',decision)
        remaining = deadline - time.clock_gettime(time.CLOCK_BOOTTIME)
        if remaining <= 0:
            return outcome('WAITING',decision)
        sleeper(min(poll,remaining))

def schedule(root, unit, delay=1, runner=subprocess.run):
    root = ipc.root_check(Path(root))
    if not re.fullmatch(r'serpantinum-v0600-ipcauto-[A-Za-z0-9]{8,24}',unit):
        raise ValueError('invalid unit')
    if type(delay) is not int or not 1 <= delay <= 30:
        raise ValueError('invalid delay')
    with ipc.locked(root) as p:
        r = ipc.read(p,'transaction.json')
        if r.get('execution_backend', 'simulated') != 'simulated':
            raise ValueError('physical transaction denied')
        if r.get('state') not in ('ARMED','APPLIED','ROLLBACK_REQUIRED','UNCERTAIN'):
            raise ValueError('invalid state')
        if r.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            raise ValueError('invalid boot')
    cmd = ['systemd-run','--user','--unit='+unit,'--on-active='+str(delay)+'s',
           '--timer-property=AccuracySec=100ms','--property=Type=oneshot',
           '--property=TimeoutStartSec=120s',sys.executable,'-B',str(Path(__file__).resolve()),
           'run',str(root),'--max-wait','90']
    p = runner(cmd,capture_output=True,text=True,timeout=15)
    if p.returncode:
        raise RuntimeError('schedule rejected')
    return outcome('SIMULATED_WATCHDOG_SCHEDULED',unit)

def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    run=sub.add_parser('run');run.add_argument('root');run.add_argument('--max-wait',type=float,default=0)
    sched=sub.add_parser('schedule');sched.add_argument('root');sched.add_argument('--unit',required=True);sched.add_argument('--delay',type=int,default=1)
    args=parser.parse_args()
    try:
        result=execute(args.root,args.max_wait) if args.action=='run' else schedule(args.root,args.unit,args.delay)
    except (OSError,RuntimeError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
        result=outcome('BLOCK_AND_INVESTIGATE',type(exc).__name__)
    print(json.dumps(result,indent=2))
    return 0 if result['decision'] in ('SIMULATED_RECOVERY_VERIFIED','CONFIRMED_NO_ACTION','SIMULATED_WATCHDOG_SCHEDULED') else 2
if __name__=='__main__':
    sys.exit(main())
