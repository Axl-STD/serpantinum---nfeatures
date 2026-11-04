#!/usr/bin/env python3
"""Offline physical execution transaction rehearsal. NEVER runs display commands.

Persists a separate rehearsal record under the existing IPC lock. No changes to
transaction.json or physical-obligation-staged.json. Not an authorization token.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
P = HERE / 'display-physical-obligation-stage-v0600.py'
if P.is_symlink() or not P.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('physical_execution_stage', P)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
ipc = stage.ipc
NAME = 'physical-execution-rehearsal.json'
STATES = ('READY_NOT_ARMED', 'APPLY_INTENT_SIMULATED', 'APPLY_OBSERVED_SIMULATED',
          'ROLLBACK_INTENT_SIMULATED', 'ROLLBACK_OBSERVED_SIMULATED')
TRANSITIONS = {
    'READY_NOT_ARMED': 'APPLY_INTENT_SIMULATED',
    'APPLY_INTENT_SIMULATED': 'APPLY_OBSERVED_SIMULATED',
    'APPLY_OBSERVED_SIMULATED': 'ROLLBACK_INTENT_SIMULATED',
    'ROLLBACK_INTENT_SIMULATED': 'ROLLBACK_OBSERVED_SIMULATED',
}
class Blocked(RuntimeError): pass

def response(decision, **extra):
    return dict(version='0.6.0', mode='physical-execution-rehearsal-only', decision=decision,
                watchdogPhysicalArmed=False, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **extra)

def check(p):
    r, s = stage.validate(p)
    if r.get('state') != 'ARMED' or r.get('phase') != 'NONE' or type(r.get('generation')) is not int or r['generation'] < 0:
        raise Blocked('invalid transaction state')
    if time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= r['deadline_boottime_ns']:
        raise Blocked('expired')
    m = ipc.read(p, stage.NAME)
    if m != stage.manifest(r, s):
        raise Blocked('staging mismatch')
    if (p / 'physical-watchdog-rehearsal.json').exists():
        raise Blocked('lab receipt collision')
    return r, s

def token(r, s):
    return dict(transaction_id=r['id'], generation=r['generation'],
                boot_id=r['boot_id'], snapshot_sha256=s['snapshot_sha256'],
                instance=s['identity']['instance'])

def record(p, r, s):
    v = ipc.read(p, NAME)
    if not isinstance(v, dict) or v.get('schema') != 1 or v.get('status') != 'REHEARSAL_ONLY_NOT_ARMED':
        raise Blocked('invalid rehearsal')
    if v.get('token') != token(r, s) or v.get('phase') not in STATES:
        raise Blocked('rehearsal identity')
    if type(v.get('step')) is not int or v['step'] < 0 or v['step'] > 4:
        raise Blocked('invalid step')
    if STATES[v['step']] != v['phase']:
        raise Blocked('phase mismatch')
    return v

def execute(root, action):
    with ipc.locked(root) as p:
        r, s = check(p)
        if action == 'prepare':
            if (p / NAME).exists() or (p / NAME).is_symlink():
                raise Blocked('already prepared')
            v = dict(schema=1, status='REHEARSAL_ONLY_NOT_ARMED', token=token(r, s),
                     phase=STATES[0], step=0)
            ipc.write(p, NAME, v)
            return response('REHEARSAL_PREPARED_NOT_ARMED', phase=v['phase'], step=0)
        v = record(p, r, s)
        if action == 'inspect':
            return response('REHEARSAL_INSPECTED_NOT_ARMED', phase=v['phase'], step=v['step'])
        if action != 'advance':
            raise Blocked('invalid action')
        if v['phase'] not in TRANSITIONS:
            return response('REHEARSAL_TERMINAL_NOT_PHYSICAL', phase=v['phase'], step=v['step'])
        v = dict(v, phase=TRANSITIONS[v['phase']], step=v['step'] + 1)
        ipc.write(p, NAME, v)
        return response('REHEARSAL_ADVANCED_NOT_PHYSICAL', phase=v['phase'], step=v['step'])

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('prepare', 'inspect', 'advance'))
    ap.add_argument('root')
    a = ap.parse_args()
    try:
        value = execute(a.root, a.action)
        code = 0
    except (Blocked, stage.Blocked, ipc.Denied, OSError, ValueError, TypeError, KeyError) as exc:
        value = response('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
        code = 2
    print(json.dumps(value, sort_keys=True))
    return code

if __name__ == '__main__':
    sys.exit(main())
