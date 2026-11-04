#!/usr/bin/env python3
"""Offline physical-recovery integration gate. No display mutations.

Integrates existing simulator/controller and read-only physical admission.
No physical execution path exists in this module.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def load(name, alias):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

sim = load('display-recovery-simulator-v0600.py', 'integration_sim')
auto = load('display-systemd-auto-rollback-v0600.py', 'integration_auto')
c = sim.controller


def prepare(root, seconds=15):
    if type(seconds) is not int or not 3 <= seconds <= 120:
        raise ValueError('invalid duration')
    root = c._root(root)
    sim.initialize(root)
    rec = c.prepare(root, sim.BASE, time.time_ns() + seconds * 1000000000)
    armed = c.transition(root, rec['id'], rec['generation'], 'ARMED')
    applied = sim.apply(root, rec['id'], armed['generation'])
    return {'state': applied['state'], 'generation': applied['generation'], 'physicalExecutionEnabled': False, 'commandsExecuted': 0}


def restore(root, now_ns=None):
    """Run simulated watchdog and restoration; never access Hyprland."""
    root = c._root(root)
    return auto.execute(root, now_ns=now_ns)


def inspect(root):
    return sim.inspect(c._root(root))


def admission(backup):
    """Run existing read-only gate; no authority to apply a mode."""
    gate = HERE / 'display-physical-supervisor-contract-v0600.py'
    if gate.is_symlink() or not gate.is_file():
        raise RuntimeError('unsafe gate')
    p = subprocess.run([sys.executable, '-B', str(gate), '--backup-dir', str(backup)],
                       capture_output=True, text=True, timeout=35,
                       env={k:v for k,v in os.environ.items() if k != 'PYTHONPATH'})
    try:
        result = json.loads(p.stdout)
    except ValueError as exc:
        raise RuntimeError('invalid gate output') from exc
    if p.returncode or result.get('decision') != 'SUPERVISOR_READONLY_READY' or result.get('physicalExecutionEnabled') is not False or result.get('commandsExecuted') != 0:
        raise RuntimeError('admission rejected')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare-simulated', 'restore-simulated', 'inspect-simulated', 'admission-readonly'])
    parser.add_argument('path')
    parser.add_argument('--seconds', type=int, default=15)
    args = parser.parse_args()
    try:
        if args.action == 'prepare-simulated':
            result = prepare(args.path, args.seconds)
        elif args.action == 'restore-simulated':
            result = restore(args.path)
        elif args.action == 'inspect-simulated':
            result = inspect(args.path)
        else:
            result = admission(args.path)
        print(json.dumps(result, indent=2))
        if args.action == 'restore-simulated' and result['decision'] not in ('SIMULATED_ROLLBACK_VERIFIED','ALREADY_ROLLED_BACK','CONFIRMED_NO_ROLLBACK'):
            return 2
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__, 'physicalExecutionEnabled':False, 'commandsExecuted':0}))
        return 2

if __name__ == '__main__':
    sys.exit(main())
