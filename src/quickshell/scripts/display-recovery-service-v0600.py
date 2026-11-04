#!/usr/bin/env python3
"""Shared recovery service for systemd and TTY3. LAB ONLY, never physical."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
DEP = HERE / 'display-physical-watchdog-runtime-lab-v0600.py'
if DEP.is_symlink() or not DEP.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('serpantinum_service_lab', DEP)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def result(decision, **extra):
    return dict(version='0.6.0', mode='recovery-service-lab-only', decision=decision,
                physicalExecutionEnabled=False, executionAuthorized=False,
                watchdogPhysicalArmed=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **extra)


def inspect(root):
    record = lab.validate(root)
    return result('LAB_OBLIGATION_OBSERVED', transactionId=record['id'],
                  state=record['state'], phase=record['phase'],
                  generation=record['generation'])


def recover(root, max_wait=90):
    before = lab.validate(root)
    response = lab.run(root, max_wait=max_wait)
    after = lab.validate(root)
    if before['id'] != after['id'] or before['boot_id'] != after['boot_id']:
        return result('BLOCK_TRANSACTION_CHANGED')
    if response['decision'] == 'LAB_RECOVERY_VERIFIED_NOT_PHYSICAL':
        if after['state'] != 'ROLLED_BACK' or after['phase'] != 'COMPLETED':
            return result('BLOCK_TERMINAL_INCONSISTENT')
    return result(response['decision'], transactionId=after['id'],
                  state=after['state'], phase=after['phase'], generation=after['generation'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'recover'))
    parser.add_argument('root')
    parser.add_argument('--max-wait', type=float, default=90)
    args = parser.parse_args(argv)
    try:
        if args.action == 'inspect':
            output = inspect(args.root)
        else:
            output = recover(args.root, args.max_wait)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        output = result('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
    print(json.dumps(output, indent=2))
    return 0 if output['decision'] in ('LAB_OBLIGATION_OBSERVED',
        'LAB_RECOVERY_VERIFIED_NOT_PHYSICAL', 'LAB_ARMED_NO_EFFECT',
        'CONFIRMED_NO_ACTION') else 2


if __name__ == '__main__':
    sys.exit(main())
