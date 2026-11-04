#!/usr/bin/env python3
"""Read-only, locked authority snapshot. Never schedules or executes monitor changes.

Captures transaction and schema-3 under the recovery lock, then validates a
freshly obtained compositor identity and read-only monitor plan. Evidence is
only a diagnostic snapshot: it is NOT a physical execution capability.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

controller = load('display-physical-recovery-controller-v0600.py', 'snap_controller')
temporal = load('display-temporal-schema3-v0600.py', 'snap_temporal')
binding = load('display-dynamic-authority-binding-v0600.py', 'snap_binding')

def snapshot(root, plan, compositor, now=None, current_boot=None):
    """No physical execution. Caller supplies evidence; freshness is not asserted."""
    with controller._locked(root) as locked:
        record = controller._read(locked)
        side = temporal._read(locked)
        if record is None or side is None:
            return blocked('BLOCK_INCOMPLETE_TRANSACTION')
        outcome = binding.decide(plan, record, side, compositor)
        if outcome['decision'] not in ('OFFLINE_BINDING_READY', 'OFFLINE_TERMINAL_BINDING'):
            return blocked(outcome['decision'])
        clock = temporal.clocks() if now is None else now
        boot = temporal.boot_id() if current_boot is None else current_boot
        if not isinstance(clock, dict) or set(clock) != {'realtime_ns', 'boottime_ns'} or any(type(v) is not int or v <= 0 for v in clock.values()):
            return blocked('BLOCK_CLOCK_SAMPLE')
        if side['boot_id'] != boot:
            return blocked('BLOCK_NEW_BOOT')
        if record['state'] == 'UNCERTAIN':
            return blocked('BLOCK_UNCERTAIN')
        if record['state'] in ('ROLLED_BACK', 'CONFIRMED'):
            decision = 'OFFLINE_TERMINAL'
        elif clock['boottime_ns'] >= side['deadline_boottime_ns']:
            decision = 'OFFLINE_RECOVERY_DUE'
        elif abs((side['deadline_realtime_ns'] - clock['realtime_ns']) - (side['deadline_boottime_ns'] - clock['boottime_ns'])) > 2_000_000_000:
            return blocked('BLOCK_CLOCK_DIVERGENCE')
        else:
            decision = 'OFFLINE_WAITING'
        return dict(outcome, decision=decision, mode='integrated-authority-snapshot',
                    captureAtomicForTransaction=True, compositorFreshnessVerified=False,
                    physicalExecutionEnabled=False, executionAuthorized=False,
                    commandsExecuted=0, rollbackPhysicalVerified=False)

def blocked(decision):
    return {'version':'0.6.0','mode':'integrated-authority-snapshot',
            'decision':decision,'captureAtomicForTransaction':False,
            'compositorFreshnessVerified':False,'physicalExecutionEnabled':False,
            'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('root')
    p.add_argument('--plan-json',required=True)
    p.add_argument('--compositor-json',required=True)
    a=p.parse_args()
    try:
        def read(path):
            path=Path(path)
            if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
                raise ValueError('unsafe evidence')
            return json.loads(path.read_text())
        result=snapshot(Path(a.root),read(a.plan_json),read(a.compositor_json))
    except (OSError,ValueError,RuntimeError,TypeError) as exc:
        result=blocked('BLOCK_AND_INVESTIGATE')
        result['errorType']=type(exc).__name__
    print(json.dumps(result,indent=2))
    return 0 if result['decision'].startswith('OFFLINE_') else 2

if __name__=='__main__':
    sys.exit(main())
