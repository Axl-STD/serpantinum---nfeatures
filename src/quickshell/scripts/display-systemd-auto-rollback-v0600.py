#!/usr/bin/env python3
"""Offline-only systemd rollback worker for a simulated monitor. No display API."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

def load(filename, key):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

sim = load('display-recovery-simulator-v0600.py', 'auto_sim')
c = sim.controller


def execute(root, now_ns=None):
    """Expire then restore, refusing missing/ambiguous/foreign or confirmed states."""
    try:
        state = c.expire(root, now_ns=now_ns)
        if state == 'NO_STATE':
            return outcome('BLOCK_NO_STATE', None)
        if state == 'CONFIRMED':
            return outcome('CONFIRMED_NO_ROLLBACK', c.inspect(root))
        if state == 'ROLLED_BACK':
            return outcome('ALREADY_ROLLED_BACK', c.inspect(root))
        if state != 'ROLLBACK_REQUIRED':
            return outcome('BLOCK_NOT_EXPIRED', c.inspect(root))
        record = c.inspect(root)
        if record['snapshot'] != sim.BASE:
            return outcome('BLOCK_SNAPSHOT_MISMATCH', record)
        # The simulator itself validates token/generation and serializes restoration.
        sim.rollback(root, record['id'], record['generation'])
        final = sim.inspect(root)
        if final['recovery']['state'] != 'ROLLED_BACK' or final['simulatedMonitor'] != sim.BASE:
            return outcome('BLOCK_VERIFICATION_FAILED', final['recovery'])
        return outcome('SIMULATED_ROLLBACK_VERIFIED', final['recovery'])
    except (OSError, ValueError, c.RecoveryDenied) as exc:
        return dict(decision='BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__,
                    physicalExecutionEnabled=False, commandsExecuted=0)


def outcome(decision, record):
    return dict(decision=decision, state=record['state'] if record else None,
                generation=record['generation'] if record else None,
                physicalExecutionEnabled=False, commandsExecuted=0,
                rollbackPhysicalVerified=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    args = parser.parse_args()
    result = execute(Path(args.root))
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('SIMULATED_ROLLBACK_VERIFIED', 'CONFIRMED_NO_ROLLBACK', 'ALREADY_ROLLED_BACK') else 2

if __name__ == '__main__':
    sys.exit(main())
