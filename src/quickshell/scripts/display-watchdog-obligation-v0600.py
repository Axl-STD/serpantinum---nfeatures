#!/usr/bin/env python3
"""Durable simulated IPC watchdog obligation. NO physical Hyprland mutations.

Receipt is advisory until externally armed; the only transaction authority is
transaction.json under the existing IPC lock. All recovery is simulation-only.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
p = HERE / 'display-ipc-transaction-lab-v0600.py'
if p.is_symlink() or not p.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('watchdog_ipc_v0600', p)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)
NAME = 'watchdog-obligation.json'
BOOT = Path('/proc/sys/kernel/random/boot_id')


def digest(r):
    return hashlib.sha256(ipc.canonical({k: r[k] for k in ('id', 'target', 'topology', 'plan_sha256', 'boot_id', 'deadline_boottime_ns')})).hexdigest()


def output(decision, r=None):
    return dict(version='0.6.0', decision=decision, state=r.get('state') if r else None,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)


def _read(p):
    r = ipc.read(p, 'transaction.json')
    if not isinstance(r, dict) or r.get('schema') != 1 or r.get('target') not in ('eDP-1', 'eDP-2'):
        raise ipc.Denied('invalid transaction')
    if r.get('boot_id') != BOOT.read_text().strip():
        raise ipc.Denied('boot mismatch')
    return r


def _receipt(p, r):
    w = ipc.read(p, NAME)
    if (not isinstance(w, dict) or set(w) != {'schema', 'transaction_id', 'generation_at_arm', 'transaction_digest', 'deadline_boottime_ns', 'boot_id', 'status'}
        or w['schema'] != 1 or w['transaction_id'] != r['id'] or w['transaction_digest'] != digest(r)
        or w['boot_id'] != r['boot_id'] or w['deadline_boottime_ns'] != r['deadline_boottime_ns']
        or w['status'] != 'ARMED_SIMULATED' or type(w['generation_at_arm']) is not int
        or w['generation_at_arm'] > r['generation']):
        raise ipc.Denied('invalid watchdog receipt')
    return w


def arm(root):
    with ipc.locked(root) as p:
        r = _read(p)
        if r['state'] != 'ARMED' or r['phase'] != 'NONE' or time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= r['deadline_boottime_ns']:
            return output('BLOCK_NOT_ARMABLE', r)
        if (p / NAME).exists():
            return output('BLOCK_ALREADY_ARMED', r)
        ipc.write(p, NAME, dict(schema=1, transaction_id=r['id'], generation_at_arm=r['generation'],
                                transaction_digest=digest(r), deadline_boottime_ns=r['deadline_boottime_ns'],
                                boot_id=r['boot_id'], status='ARMED_SIMULATED'))
        return output('SIMULATED_OBLIGATION_ARMED', r)


def recover(root):
    with ipc.locked(root) as p:
        r = _read(p)
        _receipt(p, r)
        observed = ipc.read(p, 'simulated-ipc.json')
        if observed.get('connector') != r['target'] or observed.get('mode') not in (ipc.BASE, ipc.TARGET):
            return output('BLOCK_EFFECT_MISMATCH', r)
        state, phase = r['state'], r['phase']
        if state == 'ROLLED_BACK':
            return output('SIMULATED_TERMINAL_VERIFIED' if phase == 'COMPLETED' and observed['mode'] == ipc.BASE else 'BLOCK_TERMINAL', r)
        if state == 'CONFIRMED':
            return output('CONFIRMED_NO_ACTION', r)
        if state == 'UNCERTAIN' and phase == 'ROLLBACK_INTENT' and observed['mode'] == ipc.BASE:
            r = dict(r, state='ROLLED_BACK', phase='COMPLETED', generation=r['generation'] + 1)
            ipc.write(p, 'transaction.json', r)
            return output('SIMULATED_RECONCILIATION_VERIFIED', r)
        if state == 'UNCERTAIN':
            return output('BLOCK_UNCERTAIN', r)
        if state == 'ARMED' and observed['mode'] == ipc.BASE:
            return output('SIMULATED_NOT_APPLIED', r)
        if state == 'APPLIED' and phase == 'APPLY_VERIFIED' and observed['mode'] == ipc.TARGET:
            if time.clock_gettime_ns(time.CLOCK_BOOTTIME) < r['deadline_boottime_ns']:
                return output('WAITING', r)
            r = dict(r, state='ROLLBACK_REQUIRED', generation=r['generation'] + 1)
            ipc.write(p, 'transaction.json', r)
            state = 'ROLLBACK_REQUIRED'
        if state != 'ROLLBACK_REQUIRED' or phase != 'APPLY_VERIFIED':
            return output('BLOCK_STATE', r)
        r = dict(r, state='UNCERTAIN', phase='ROLLBACK_INTENT', generation=r['generation'] + 1)
        ipc.write(p, 'transaction.json', r)
        ipc.write(p, 'simulated-ipc.json', {'connector': r['target'], 'mode': ipc.BASE})
        if ipc.read(p, 'simulated-ipc.json') != {'connector': r['target'], 'mode': ipc.BASE}:
            return output('BLOCK_READBACK', r)
        r = dict(r, state='ROLLED_BACK', phase='COMPLETED', generation=r['generation'] + 1)
        ipc.write(p, 'transaction.json', r)
        return output('SIMULATED_ROLLBACK_VERIFIED', r)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('arm', 'recover', 'inspect'))
    ap.add_argument('root')
    args = ap.parse_args()
    try:
        if args.action == 'arm':
            result = arm(args.root)
        elif args.action == 'recover':
            result = recover(args.root)
        else:
            with ipc.locked(args.root) as p:
                r = _read(p)
                _receipt(p, r)
                result = output('SIMULATED_OBLIGATION_PRESENT', r)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
        result = output('BLOCK_AND_INVESTIGATE')
        result['errorType'] = type(e).__name__
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('SIMULATED_OBLIGATION_ARMED', 'SIMULATED_OBLIGATION_PRESENT', 'SIMULATED_ROLLBACK_VERIFIED', 'SIMULATED_RECONCILIATION_VERIFIED', 'SIMULATED_TERMINAL_VERIFIED', 'CONFIRMED_NO_ACTION', 'SIMULATED_NOT_APPLIED') else 2

if __name__ == '__main__':
    sys.exit(main())
