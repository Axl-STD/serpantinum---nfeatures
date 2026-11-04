#!/usr/bin/env python3
"""Offline watchdog transaction lifecycle. No monitor operations or recovery claims."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent

def load(name, key):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

controller = load('display-physical-recovery-controller-v0600.py', 'transaction_controller')
watchdog = load('display-recovery-watchdog-v0600.py', 'transaction_watchdog')

def snapshot():
    return dict(name='eDP-1', width=1920, height=1080, refresh_mhz=60060, scale_milli=1000, transform=0)

def prepare_armed(root, seconds=3):
    root = Path(root)
    if not root.is_absolute():
        raise controller.RecoveryDenied('root must be absolute')
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise controller.RecoveryDenied('invalid timeout')
    record = controller.prepare(root, snapshot(), time.time_ns() + seconds * 1_000_000_000)
    return controller.transition(root, record['id'], record['generation'], 'ARMED')

def run_watchdog(root):
    return watchdog.decision(root)

def launch_systemd(root, unit, delay='3s'):
    """Explicit opt-in: schedule only the offline decision, never physical commands."""
    root = controller._root(root)
    if not unit.startswith('serpantinum-v0600-txn-') or not unit.replace('-', '').isalnum():
        raise ValueError('invalid unit')
    if delay not in ('3s', '5s', '10s', '20s'):
        raise ValueError('invalid delay')
    command = ['systemd-run', '--user', '--unit=' + unit, '--on-active=' + delay,
               '--property=Type=oneshot', '--property=TimeoutStartSec=30s',
               sys.executable, '-B', str(Path(__file__).resolve()), 'expire', str(root)]
    return subprocess.run(command, capture_output=True, text=True, timeout=15)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare-armed', 'expire', 'inspect', 'schedule-offline'))
    parser.add_argument('root')
    parser.add_argument('--seconds', type=int, default=3)
    parser.add_argument('--unit')
    parser.add_argument('--delay', default='3s')
    args = parser.parse_args()
    try:
        if args.action == 'prepare-armed':
            result = prepare_armed(args.root, args.seconds)
        elif args.action == 'expire':
            result = run_watchdog(args.root)
        elif args.action == 'inspect':
            result = controller.inspect(args.root)
        else:
            if not args.unit:
                raise ValueError('unit required')
            p = launch_systemd(args.root, args.unit, args.delay)
            result = dict(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
        print(json.dumps(result, indent=2))
        return 0 if not isinstance(result, dict) or result.get('returncode', 0) == 0 else 2
    except (OSError, ValueError, controller.RecoveryDenied, subprocess.TimeoutExpired) as exc:
        print(json.dumps(dict(errorType=type(exc).__name__, error=str(exc), physicalExecutionEnabled=False, commandsExecuted=0)))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
