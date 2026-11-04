#!/usr/bin/env python3
"""Simulated Hyprland adapter on the unified IPC transaction store.

No live mutation API exists here. The adapter can only mutate the private
simulated-ipc.json fixture; all physical commands are rejected.
"""
import importlib.util
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
p = HERE / 'display-ipc-transaction-lab-v0600.py'
if p.is_symlink() or not p.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('physical_adapter_ipc', p)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)

class Denied(RuntimeError):
    pass

ORIGINAL = ipc.BASE
CANDIDATE = ipc.TARGET


def verify_observation(record, observed):
    if not isinstance(record, dict) or not isinstance(observed, dict):
        raise Denied('invalid observation')
    if record.get('target') not in ('eDP-1', 'eDP-2'):
        raise Denied('invalid connector')
    if observed.get('connector') != record['target']:
        raise Denied('connector drift')
    if observed.get('mode') not in (ORIGINAL, CANDIDATE):
        raise Denied('unknown mode')
    if record.get('topology') not in ('internal-only', 'internal-and-hdmi'):
        raise Denied('topology drift')
    return observed['mode']


def _read(root):
    record = ipc.read(root, 'transaction.json')
    observed = ipc.read(root, 'simulated-ipc.json')
    verify_observation(record, observed)
    return record, observed


def _boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def execute(root, operation, token, now_ns=None, failpoint=None, boot_id=None):
    """Apply/restore only the simulated effect, using the original IPC lock.

    A real physical backend must be a different implementation with separate
    authorization and watchdog. Never interpret this output as physical proof.
    """
    if operation not in ('apply', 'recover', 'inspect'):
        raise Denied('physical operations are not implemented')
    if failpoint not in (None, 'after-intent', 'after-effect'):
        raise Denied('invalid failpoint')
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_ns is None else now_ns
    boot = _boot() if boot_id is None else boot_id
    with ipc.locked(root) as path:
        record, observed = _read(path)
        if record.get('id') != token or record.get('boot_id') != boot:
            raise Denied('transaction identity changed')
        state, phase = record.get('state'), record.get('phase')
        mode = observed['mode']
        if operation == 'inspect':
            return outcome('SIMULATED_OBSERVATION', record, mode)
        if state in ('ROLLED_BACK', 'CONFIRMED'):
            if state == 'ROLLED_BACK' and (phase != 'COMPLETED' or mode != ORIGINAL):
                raise Denied('terminal inconsistent')
            return outcome('TERMINAL_NO_ACTION', record, mode)
        if operation == 'apply':
            if state != 'ARMED' or phase != 'NONE' or mode != ORIGINAL or now >= record['deadline_boottime_ns']:
                raise Denied('apply not permitted')
            record = dict(record, state='UNCERTAIN', phase='APPLY_INTENT', generation=record['generation'] + 1)
            ipc.write(path, 'transaction.json', record)
            if failpoint == 'after-intent': return outcome('INTERRUPTED_APPLY', record, mode)
            ipc.write(path, 'simulated-ipc.json', {'connector': record['target'], 'mode': CANDIDATE})
            if failpoint == 'after-effect': return outcome('INTERRUPTED_APPLY', record, CANDIDATE)
            record = dict(record, state='APPLIED', phase='APPLY_VERIFIED', generation=record['generation'] + 1)
            ipc.write(path, 'transaction.json', record)
            return outcome('SIMULATED_APPLY_VERIFIED', record, CANDIDATE)
        # Recover. No rollback is attempted for interrupted application.
        if state == 'UNCERTAIN':
            if phase != 'ROLLBACK_INTENT' or mode != ORIGINAL:
                raise Denied('ambiguous effect')
            record = dict(record, state='ROLLED_BACK', phase='COMPLETED', generation=record['generation'] + 1)
            ipc.write(path, 'transaction.json', record)
            return outcome('SIMULATED_RECONCILIATION_VERIFIED', record, ORIGINAL)
        if state == 'APPLIED':
            if phase != 'APPLY_VERIFIED' or mode != CANDIDATE or now < record['deadline_boottime_ns']:
                raise Denied('recovery not due')
            record = dict(record, state='ROLLBACK_REQUIRED', generation=record['generation'] + 1)
            ipc.write(path, 'transaction.json', record)
        if record['state'] != 'ROLLBACK_REQUIRED' or record['phase'] != 'APPLY_VERIFIED' or mode not in (ORIGINAL, CANDIDATE):
            raise Denied('invalid rollback state')
        record = dict(record, state='UNCERTAIN', phase='ROLLBACK_INTENT', generation=record['generation'] + 1)
        ipc.write(path, 'transaction.json', record)
        if failpoint == 'after-intent': return outcome('INTERRUPTED_ROLLBACK', record, mode)
        ipc.write(path, 'simulated-ipc.json', {'connector': record['target'], 'mode': ORIGINAL})
        if failpoint == 'after-effect': return outcome('INTERRUPTED_ROLLBACK', record, ORIGINAL)
        if ipc.read(path, 'simulated-ipc.json') != {'connector': record['target'], 'mode': ORIGINAL}:
            raise Denied('readback mismatch')
        record = dict(record, state='ROLLED_BACK', phase='COMPLETED', generation=record['generation'] + 1)
        ipc.write(path, 'transaction.json', record)
        return outcome('SIMULATED_ROLLBACK_VERIFIED', record, ORIGINAL)


def outcome(decision, record, observed):
    return {'version': '0.6.0', 'decision': decision, 'state': record['state'],
            'phase': record['phase'], 'generation': record['generation'],
            'target': record['target'], 'observedMode': observed,
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'commandsExecuted': 0, 'rollbackPhysicalVerified': False}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--inspect', action='store_true', required=True)
    args = ap.parse_args()
    try:
        with ipc.locked(args.root) as root:
            record, observed = _read(root)
            print(json.dumps(outcome('SIMULATED_OBSERVATION', record, observed['mode']), indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(json.dumps({'decision': 'BLOCK_AND_INVESTIGATE', 'errorType': type(exc).__name__,
                          'physicalExecutionEnabled': False, 'commandsExecuted': 0}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
