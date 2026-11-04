#!/usr/bin/env python3
"""Offline-only unified IPC recovery worker. Never invokes Hyprland or physical commands.

Uses the IPC lab's one lock and one transaction record. The old recovery.json
backend is intentionally not imported or synchronized.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
P = HERE / 'display-ipc-transaction-lab-v0600.py'
if P.is_symlink() or not P.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('unified_ipc_lab', P)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)


def outcome(decision, record=None):
    return {'version':'0.6.0','mode':'unified-ipc-recovery-offline',
            'decision':decision,'state':record.get('state') if record else None,
            'generation':record.get('generation') if record else None,
            'physicalExecutionEnabled':False,'executionAuthorized':False,
            'commandsExecuted':0,'rollbackPhysicalVerified':False}


def execute(root, now_ns=None, boot_id=None):
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_ns is None else now_ns
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip() if boot_id is None else boot_id
    with ipc.locked(root) as p:
        try:
            r = ipc.read(p, 'transaction.json')
            observed = ipc.read(p, 'simulated-ipc.json')
        except (OSError, ValueError, TypeError):
            return outcome('BLOCK_INCOMPLETE_OR_CORRUPT')
        if (not isinstance(r, dict) or not isinstance(observed, dict)
            or type(r.get('generation')) is not int or r['generation'] < 1
            or type(r.get('deadline_boottime_ns')) is not int
            or not isinstance(r.get('id'),str) or len(r['id']) != 64
            or r.get('target') not in ('eDP-1','eDP-2')
            or observed.get('connector') != r['target']
            or observed.get('mode') not in (ipc.BASE,ipc.TARGET)
            or r.get('phase') not in ('NONE','APPLY_INTENT','APPLY_VERIFIED','ROLLBACK_INTENT','COMPLETED')
            or r.get('state') not in ipc.STATES):
            return outcome('BLOCK_INVALID_STATE', r)
        if r.get('execution_backend', 'simulated') != 'simulated':
            return outcome('BLOCK_PHYSICAL_TRANSACTION', r)
        if r.get('boot_id') != boot:
            return outcome('BLOCK_NEW_BOOT', r)
        state, phase, mode = r['state'],r['phase'],observed['mode']
        if state == 'CONFIRMED':
            return outcome('CONFIRMED_NO_ACTION',r)
        if state == 'ROLLED_BACK':
            return outcome('TERMINAL_VERIFIED' if phase == 'COMPLETED' and mode == ipc.BASE else 'BLOCK_TERMINAL_INCONSISTENT',r)
        if state == 'PREPARED':
            return outcome('BLOCK_NOT_APPLIED',r)
        if state == 'ARMED':
            return outcome('BLOCK_ARMED_NO_EFFECT' if mode == ipc.BASE else 'BLOCK_ARMED_MISMATCH',r)
        if state == 'APPLIED' and phase == 'APPLY_VERIFIED' and mode == ipc.TARGET:
            if now < r['deadline_boottime_ns']:
                return outcome('WAITING',r)
            r = dict(r,state='ROLLBACK_REQUIRED',generation=r['generation']+1)
            ipc.write(p,'transaction.json',r)
            state='ROLLBACK_REQUIRED'
        if state == 'ROLLBACK_REQUIRED' and phase == 'APPLY_VERIFIED' and mode in (ipc.BASE,ipc.TARGET):
            # Durable intent precedes effect. Crash leaves an explicit phase.
            r = dict(r,state='UNCERTAIN',phase='ROLLBACK_INTENT',generation=r['generation']+1)
            ipc.write(p,'transaction.json',r)
            ipc.write(p,'simulated-ipc.json',{'connector':r['target'],'mode':ipc.BASE})
            if ipc.read(p,'simulated-ipc.json') != {'connector':r['target'],'mode':ipc.BASE}:
                return outcome('BLOCK_VERIFICATION',r)
            r = dict(r,state='ROLLED_BACK',phase='COMPLETED',generation=r['generation']+1)
            ipc.write(p,'transaction.json',r)
            return outcome('SIMULATED_ROLLBACK_VERIFIED',r)
        if state == 'UNCERTAIN' and phase == 'ROLLBACK_INTENT':
            # Effect may have happened. Only complete if original is observed.
            if mode != ipc.BASE:
                return outcome('BLOCK_UNCERTAIN_EFFECT',r)
            r = dict(r,state='ROLLED_BACK',phase='COMPLETED',generation=r['generation']+1)
            ipc.write(p,'transaction.json',r)
            return outcome('SIMULATED_RECONCILIATION_VERIFIED',r)
        if state == 'UNCERTAIN' and phase == 'APPLY_INTENT':
            # Never infer an applied or rolled-back state from an interrupted apply.
            return outcome('BLOCK_UNCERTAIN_APPLY',r)
        return outcome('BLOCK_INCONSISTENT_PHASE',r)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root')
    args=parser.parse_args()
    try:
        result=execute(Path(args.root))
    except (OSError,RuntimeError,ValueError,TypeError,KeyError) as exc:
        result=outcome('BLOCK_AND_INVESTIGATE')
        result['errorType']=type(exc).__name__
    print(json.dumps(result,indent=2))
    return 0 if result['decision'] in ('SIMULATED_ROLLBACK_VERIFIED','SIMULATED_RECONCILIATION_VERIFIED','TERMINAL_VERIFIED','CONFIRMED_NO_ACTION') else 2

if __name__=='__main__':
    sys.exit(main())
