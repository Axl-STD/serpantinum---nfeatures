#!/usr/bin/env python3
"""Simulated watchdog systemd coordinator. Never operates on Hyprland.

A scheduled timer is not a physical safety guarantee. Only simulation is supported.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
p = HERE / 'display-watchdog-obligation-v0600.py'
if p.is_symlink() or not p.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('wd_obligation', p)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)
ipc = wd.ipc
PREFIX = 'serpantinum-v0600-wdob-'
SUCCESS = {'SIMULATED_ROLLBACK_VERIFIED', 'SIMULATED_RECONCILIATION_VERIFIED',
           'SIMULATED_TERMINAL_VERIFIED', 'CONFIRMED_NO_ACTION', 'SIMULATED_NOT_APPLIED'}


def result(decision, detail=None):
    return dict(version='0.6.0', mode='watchdog-systemd-simulation', decision=decision,
                detail=detail, physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)


def inspect(root):
    with ipc.locked(root) as path:
        record = wd._read(path)
        wd._receipt(path, record)
        return record


def run(root, max_wait=90, poll=0.1, sleep=time.sleep):
    if type(max_wait) not in (int, float) or not 0 <= max_wait <= 120:
        raise ValueError('invalid max_wait')
    if type(poll) not in (int, float) or not 0.02 <= poll <= 2:
        raise ValueError('invalid poll')
    initial = inspect(root)
    token, boot = initial['id'], initial['boot_id']
    end = time.monotonic() + max_wait
    while True:
        record = inspect(root)
        if record['id'] != token or record['boot_id'] != boot:
            return result('BLOCK_IDENTITY_CHANGED')
        response = wd.recover(root)
        decision = response['decision']
        if decision in SUCCESS:
            check = inspect(root)
            if check['id'] != token or check['boot_id'] != boot:
                return result('BLOCK_IDENTITY_CHANGED')
            if decision in ('SIMULATED_NOT_APPLIED', 'CONFIRMED_NO_ACTION'):
                return result(decision)
            with ipc.locked(root) as path:
                effect = ipc.read(path, 'simulated-ipc.json')
                if check['state'] != 'ROLLED_BACK' or check['phase'] != 'COMPLETED' or effect != {'connector': check['target'], 'mode': ipc.BASE}:
                    return result('BLOCK_FINAL_VERIFICATION')
            return result('SIMULATED_RECOVERY_VERIFIED', decision)
        if decision != 'WAITING':
            return result('BLOCK_AND_INVESTIGATE', decision)
        remaining = min(end - time.monotonic(), (record['deadline_boottime_ns'] - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9)
        if remaining <= 0:
            if time.monotonic() >= end:
                return result('WAITING')
            continue
        sleep(min(poll, remaining))


def schedule(root, unit, delay=1, runner=subprocess.run):
    if not isinstance(unit, str) or not re.fullmatch(PREFIX + r'[A-Za-z0-9]{8,24}', unit):
        raise ValueError('invalid unit')
    if type(delay) is not int or not 1 <= delay <= 30:
        raise ValueError('invalid delay')
    record = inspect(root)
    if record['state'] != 'ARMED' or record['phase'] != 'NONE':
        raise ValueError('not armable')
    if time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= record['deadline_boottime_ns']:
        raise ValueError('deadline expired')
    root = ipc.root_check(Path(root))
    command = ['systemd-run', '--user', '--unit=' + unit, '--on-active=' + str(delay) + 's',
               '--timer-property=AccuracySec=100ms', '--property=Type=oneshot',
               '--property=TimeoutStartSec=120s', sys.executable, '-B', str(Path(__file__).resolve()),
               'run', str(root), '--max-wait', '90']
    proc = runner(command, capture_output=True, text=True, timeout=15)
    if proc.returncode != 0:
        return result('BLOCK_SCHEDULE_FAILED')
    # Verify timer registration; service result alone is not sufficient.
    verify = runner(['systemctl', '--user', 'show', unit + '.timer', '-p', 'LoadState', '-p', 'ActiveState', '--no-pager'],
                    capture_output=True, text=True, timeout=15)
    if verify.returncode != 0 or 'LoadState=loaded' not in verify.stdout or 'ActiveState=active' not in verify.stdout:
        return result('BLOCK_TIMER_NOT_VERIFIED')
    # Re-check obligation and transaction after external scheduling.
    after = inspect(root)
    if after['id'] != record['id'] or after['boot_id'] != record['boot_id'] or after['state'] != 'ARMED' or after['generation'] != record['generation']:
        return result('BLOCK_STATE_CHANGED_AFTER_SCHEDULE')
    return result('SIMULATED_TIMER_VERIFIED', unit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    r = sub.add_parser('run'); r.add_argument('root'); r.add_argument('--max-wait', type=float, default=90)
    s = sub.add_parser('schedule'); s.add_argument('root'); s.add_argument('--unit', required=True); s.add_argument('--delay', type=int, default=1)
    args = parser.parse_args()
    try:
        value = run(args.root, args.max_wait) if args.action == 'run' else schedule(args.root, args.unit, args.delay)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        value = result('BLOCK_AND_INVESTIGATE', type(exc).__name__)
    print(json.dumps(value, indent=2))
    return 0 if value['decision'] in ('SIMULATED_TIMER_VERIFIED', 'SIMULATED_RECOVERY_VERIFIED', 'CONFIRMED_NO_ACTION', 'SIMULATED_NOT_APPLIED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
