#!/usr/bin/env python3
"""Fail-closed recovery coordinator for offline execution rehearsals.

Does not observe, apply, or restore physical monitors. No physical authorization.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
DEP = HERE / 'display-physical-execution-transaction-v0600.py'
if DEP.is_symlink() or not DEP.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('tx_recovery_execution', DEP)
tx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tx)
ipc = tx.ipc

class Blocked(RuntimeError): pass

def result(decision, **extra):
    return dict(version='0.6.0', mode='offline-recovery-coordinator',
                decision=decision, watchdogPhysicalArmed=False,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False, **extra)

def execute(root, action):
    if action not in ('inspect', 'recover'):
        raise Blocked('unknown action')
    with ipc.locked(root) as p:
        r, s = tx.check(p)
        v = tx.record(p, r, s)
        phase = v['phase']
        if action == 'inspect':
            return result('OFFLINE_REHEARSAL_OBSERVED', phase=phase, step=v['step'])
        if phase == 'READY_NOT_ARMED':
            return result('NO_EFFECT_RECORDED_NOT_ARMED', phase=phase)
        if phase == 'APPLY_INTENT_SIMULATED':
            return result('UNCERTAIN_EFFECT_REQUIRES_EVIDENCE', phase=phase)
        if phase == 'APPLY_OBSERVED_SIMULATED':
            # Only persist a rollback *intention*, never an observed effect.
            next_phase = tx.TRANSITIONS[phase]
            if next_phase != 'ROLLBACK_INTENT_SIMULATED':
                raise Blocked('unexpected transition')
            v = dict(v, phase=next_phase, step=v['step'] + 1)
            ipc.write(p, tx.NAME, v)
            return result('ROLLBACK_INTENT_PERSISTED_NOT_EXECUTED', phase=next_phase, step=v['step'])
        if phase == 'ROLLBACK_INTENT_SIMULATED':
            return result('ROLLBACK_PENDING_EXTERNAL_EVIDENCE', phase=phase)
        if phase == 'ROLLBACK_OBSERVED_SIMULATED':
            return result('SIMULATED_TERMINAL_NOT_PHYSICAL', phase=phase)
        raise Blocked('invalid phase')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('inspect', 'recover'))
    ap.add_argument('root')
    args = ap.parse_args()
    try:
        value = execute(args.root, args.action)
        rc = 0
    except (Blocked, tx.Blocked, tx.stage.Blocked, ipc.Denied,
            OSError, ValueError, KeyError, TypeError) as exc:
        value = result('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
        rc = 2
    print(json.dumps(value, sort_keys=True), flush=True)
    return rc

if __name__ == '__main__':
    sys.exit(main())
