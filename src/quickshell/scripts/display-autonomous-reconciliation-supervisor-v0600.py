#!/usr/bin/env python3
"""Bounded, offline-only BOOTTIME supervisor; no display APIs or physical authority."""
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
    p = HERE / filename
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

bridge = load('display-phase-boottime-bridge-v0600.py', 'autonomous_bridge')
reconcile = load('display-phase-reconcile-v0600.py', 'autonomous_reconcile')
temporal = load('display-temporal-schema3-v0600.py', 'autonomous_temporal')
SUCCESS = {'SIMULATED_ROLLBACK_VERIFIED', 'SIMULATED_RECONCILIATION_VERIFIED',
           'TERMINAL_VERIFIED', 'TERMINAL_JOURNAL_REPAIRED'}

def output(decision, detail=None):
    return dict(version='0.6.0', mode='autonomous-reconciliation-simulated',
                decision=decision, detail=detail,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)

def verify(root):
    with bridge.c._locked(Path(root)) as path:
        r = bridge.c._read(path)
        side = temporal._read(path)
        journal = bridge.phase.read(path)
        observed = bridge.sim._observed(path)
        if (not r or not side or not journal or r['state'] != 'ROLLED_BACK'
                or observed != bridge.sim.BASE or journal['phase'] != 'COMPLETED'
                or journal['generation'] != r['generation']
                or journal['transaction_id'] != r['id']
                or side['transaction_id'] != r['id']
                or side['boot_id'] != temporal.boot_id()
                or journal['boot_id'] != temporal.boot_id()
                or journal['observed_digest'] != bridge.phase.digest(bridge.sim.BASE)):
            return False
        return True

def execute(root, max_wait=0, poll=0.1, sleeper=time.sleep):
    if type(max_wait) not in (int, float) or not 0 <= max_wait <= 120:
        raise ValueError('max_wait')
    if type(poll) not in (int, float) or not 0.02 <= poll <= 2:
        raise ValueError('poll')
    root = bridge.c._root(Path(root))
    initial = bridge.c.inspect(root)
    if initial is None:
        return output('BLOCK_NO_STATE')
    token = initial['id']
    end = time.monotonic() + max_wait
    while True:
        # Check identity every iteration, without enforcing a fixed generation:
        # the bridge legitimately advances it while restoring.
        current = bridge.c.inspect(root)
        if current is None or current['id'] != token:
            return output('BLOCK_IDENTITY_CHANGED')
        if current['state'] == 'ROLLED_BACK':
            result = reconcile.execute(root, expected_token=token)
            return output('VERIFIED_TERMINAL' if result['decision'] in SUCCESS and verify(root)
                          else 'BLOCK_TERMINAL_INCONSISTENT', result['decision'])
        if current['state'] == 'CONFIRMED':
            return output('CONFIRMED_NO_ACTION')
        if current['state'] == 'UNCERTAIN':
            result = reconcile.execute(root, expected_token=token)
        else:
            result = bridge.execute(root, expected_token=token)
            if result['decision'] == 'BLOCK_UNCERTAIN':
                result = reconcile.execute(root, expected_token=token)
        decision = result['decision']
        if decision in SUCCESS:
            return output('SIMULATED_RECOVERY_VERIFIED' if verify(root)
                          else 'BLOCK_FINAL_VERIFICATION', decision)
        if decision == 'TERMINAL_NO_ACTION':
            return output('VERIFIED_TERMINAL' if verify(root)
                          else 'BLOCK_TERMINAL_INCONSISTENT', decision)
        if decision == 'WAITING':
            remaining = end - time.monotonic()
            if remaining <= 0:
                return output('WAITING', decision)
            sleeper(min(poll, remaining))
            continue
        return output('BLOCK_AND_INVESTIGATE', decision)

def schedule(root, unit, delay=1, runner=subprocess.run):
    root = bridge.c._root(Path(root))
    if not re.fullmatch(r'serpantinum-v0600-autoreconcile-[a-zA-Z0-9]{8,32}', unit):
        raise ValueError('unit')
    if type(delay) is not int or not 1 <= delay <= 30:
        raise ValueError('delay')
    record = bridge.c.inspect(root)
    if record is None or record['state'] not in ('APPLIED', 'ROLLBACK_REQUIRED', 'UNCERTAIN'):
        raise ValueError('state')
    with bridge.c._locked(root) as path:
        side = temporal._read(path)
        if not side or side['transaction_id'] != record['id'] or side['boot_id'] != temporal.boot_id():
            raise ValueError('identity')
    command = ['systemd-run', '--user', '--unit='+unit, '--on-active='+str(delay)+'s',
               '--timer-property=AccuracySec=100ms', '--property=Type=oneshot',
               '--property=TimeoutStartSec=120s', sys.executable, '-B', str(Path(__file__).resolve()),
               'run', str(root), '--max-wait', '90']
    proc = runner(command, capture_output=True, text=True, timeout=15)
    if proc.returncode:
        raise RuntimeError('systemd scheduling failed')
    return output('SIMULATED_WATCHDOG_SCHEDULED', unit)

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run'); run.add_argument('root'); run.add_argument('--max-wait', type=float, default=0)
    sched = sub.add_parser('schedule'); sched.add_argument('root'); sched.add_argument('--unit', required=True); sched.add_argument('--delay', type=int, default=1)
    a = p.parse_args()
    try:
        result = execute(a.root, a.max_wait) if a.action == 'run' else schedule(a.root, a.unit, a.delay)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        result = output('BLOCK_AND_INVESTIGATE', type(exc).__name__)
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] in ('SIMULATED_RECOVERY_VERIFIED', 'VERIFIED_TERMINAL',
                                       'CONFIRMED_NO_ACTION', 'SIMULATED_WATCHDOG_SCHEDULED') else 2

if __name__ == '__main__':
    sys.exit(main())
