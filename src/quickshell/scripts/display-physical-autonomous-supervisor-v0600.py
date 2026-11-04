#!/usr/bin/env python3
"""Offline-only automatic rollback scheduling. No physical display commands.

The watchdog service invokes the existing simulated rollback worker directly.
The deadline is wall-clock based: NOT safe for physical display execution.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    p = HERE / filename
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    s = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

integration = load('display-physical-recovery-integration-v0600.py', 'autonomous_integration')
worker = HERE / 'display-systemd-auto-rollback-v0600.py'

class SupervisorDenied(RuntimeError):
    pass

def plan(root, unit, delay_seconds=8, runner=subprocess.run):
    root = integration.c._root(root)
    if not re.fullmatch(r'serpantinum-v0600-autosim-[a-zA-Z0-9]{8,40}', unit):
        raise SupervisorDenied('invalid unit name')
    if type(delay_seconds) is not int or not 5 <= delay_seconds <= 120:
        raise SupervisorDenied('invalid delay')
    if worker.is_symlink() or not worker.is_file():
        raise SupervisorDenied('missing worker')
    record = integration.c.inspect(root)
    if record is None or record['state'] != 'APPLIED':
        raise SupervisorDenied('not applied')
    if record['snapshot'] != integration.sim.BASE:
        raise SupervisorDenied('unexpected snapshot')
    if integration.inspect(root)['simulatedMonitor'] != integration.sim.TARGET:
        raise SupervisorDenied('unexpected simulated effect')
    # Must schedule before deadline; caller must allocate enough time.
    import time
    remaining = (record['deadline_ns'] - time.time_ns()) / 1e9
    if remaining <= delay_seconds + 2:
        raise SupervisorDenied('insufficient watchdog margin')
    return ['systemd-run', '--user', '--unit='+unit,
            '--on-active='+str(delay_seconds)+'s', '--property=Type=oneshot',
            '--property=TimeoutStartSec=30s', sys.executable, '-B', str(worker), str(root)]

def schedule(root, unit, delay_seconds=8, runner=subprocess.run):
    command = plan(root, unit, delay_seconds)
    result = runner(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SupervisorDenied('watchdog scheduling failed')
    return {'decision':'SIMULATED_WATCHDOG_SCHEDULED', 'unit':unit,
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('root')
    p.add_argument('--unit', required=True)
    p.add_argument('--delay', type=int, default=8)
    args = p.parse_args()
    try:
        result = schedule(Path(args.root), args.unit, args.delay)
        status = 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result = {'decision':'BLOCK_AND_INVESTIGATE', 'errorType':type(exc).__name__,
                  'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
        status = 2
    print(json.dumps(result, indent=2))
    return status

if __name__ == '__main__':
    sys.exit(main())
