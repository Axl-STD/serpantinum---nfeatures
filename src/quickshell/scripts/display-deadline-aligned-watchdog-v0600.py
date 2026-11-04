#!/usr/bin/env python3
"""Offline-only deadline-aligned watchdog. No display API or physical operations."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
class DeadlineDenied(RuntimeError): pass

def dependency(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file(): raise DeadlineDenied('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def plan(root, unit, now_ns=None):
    if not re.fullmatch(r'serpantinum-v0600-deadline-[A-Za-z0-9]{8,40}', unit):
        raise DeadlineDenied('invalid unit')
    integration = dependency('display-physical-recovery-integration-v0600.py','deadline_integration')
    root = integration.c._root(Path(root))
    state = integration.c.inspect(root)
    if not state or state['state'] != 'APPLIED' or state['snapshot'] != integration.sim.BASE:
        raise DeadlineDenied('not eligible')
    if integration.inspect(root)['simulatedMonitor'] != integration.sim.TARGET:
        raise DeadlineDenied('unexpected effect')
    now = time.time_ns() if now_ns is None else now_ns
    remaining = state['deadline_ns'] - now
    if remaining <= 2_000_000_000 or remaining > 600_000_000_000:
        raise DeadlineDenied('unsafe deadline margin')
    worker = HERE / 'display-deadline-aligned-worker-v0600.py'
    if worker.is_symlink() or not worker.is_file(): raise DeadlineDenied('missing worker')
    # Schedule slightly early; the worker waits until the authoritative deadline.
    seconds = max(1, math.ceil(remaining / 1e9) - 1)
    command = ['systemd-run','--user','--unit='+unit,'--on-active='+str(seconds)+'s',
               '--timer-property=AccuracySec=100ms','--property=Type=oneshot',
               '--property=TimeoutStartSec=650s',sys.executable,'-B',str(worker),str(root)]
    return command

def schedule(root, unit, runner=subprocess.run):
    cmd = plan(root, unit)
    result = runner(cmd,capture_output=True,text=True,timeout=15)
    if result.returncode: raise DeadlineDenied('schedule failed')
    return {'decision':'DEADLINE_WATCHDOG_SCHEDULED','unit':unit,
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--unit',required=True)
    a=p.parse_args()
    try: result=schedule(a.root,a.unit);rc=0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'physicalExecutionEnabled':False,'commandsExecuted':0};rc=2
    print(json.dumps(result,indent=2));return rc
if __name__=='__main__':sys.exit(main())
