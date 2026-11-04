#!/usr/bin/env python3
"""Offline BOOTTIME recovery bridge. No display commands, no scheduling authority."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent

def load(name,alias):
    path=HERE/name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec=importlib.util.spec_from_file_location(alias,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sim=load('display-recovery-simulator-v0600.py','bridge_sim')
temporal=load('display-temporal-schema3-v0600.py','bridge_temporal')
controller=sim.controller

class BridgeDenied(RuntimeError):pass

def result(decision, record=None):
    return {'version':'0.6.0','mode':'portable-runtime-bridge-offline',
            'decision':decision,'state':record['state'] if record else None,
            'generation':record['generation'] if record else None,
            'physicalExecutionEnabled':False,'executionAuthorized':False,
            'commandsExecuted':0,'rollbackPhysicalVerified':False}

def execute(root, sample=None, current_boot=None, expected_token=None, expected_generation=None):
    """Atomically inspect time and set recovery due; simulated effect follows via fencing.

    Caller cannot authorize physical execution. Incomplete/ambiguous state fails closed.
    """
    root=Path(root)
    sample=temporal.clocks() if sample is None else sample
    boot=temporal.boot_id() if current_boot is None else current_boot
    if not isinstance(sample,dict) or set(sample)!={'realtime_ns','boottime_ns'} or any(type(x) is not int or x<=0 for x in sample.values()):
        raise BridgeDenied('invalid clocks')
    with controller._locked(root) as locked:
        record=controller._read(locked)
        side=temporal._read(locked)
        if record is None or side is None:
            return result('BLOCK_INCOMPLETE_TRANSACTION',record)
        if (side['transaction_id']!=record['id'] or side['deadline_realtime_ns']!=record['deadline_ns']
            or record['generation']<side['generation_at_binding'] or side['recovery_required'] is not True):
            return result('BLOCK_IDENTITY_MISMATCH',record)
        if expected_token is not None and expected_token!=record['id']:
            return result('BLOCK_IDENTITY_MISMATCH',record)
        if expected_generation is not None and expected_generation!=record['generation']:
            return result('BLOCK_STALE_GENERATION',record)
        if side['boot_id']!=boot:
            return result('BLOCK_NEW_BOOT',record)
        if record['state']=='UNCERTAIN':
            return result('BLOCK_UNCERTAIN',record)
        if record['state'] in ('CONFIRMED','ROLLED_BACK'):
            return result('TERMINAL_NO_ACTION',record)
        if record['snapshot']!=sim.BASE:
            return result('BLOCK_SNAPSHOT_MISMATCH',record)
        if sample['boottime_ns']<side['deadline_boottime_ns']:
            if abs((side['deadline_realtime_ns']-sample['realtime_ns'])-(side['deadline_boottime_ns']-sample['boottime_ns']))>2_000_000_000:
                return result('BLOCK_CLOCK_DIVERGENCE',record)
            return result('WAITING',record)
        if record['state'] in ('PREPARED','ARMED','APPLIED'):
            record=dict(record,state='ROLLBACK_REQUIRED',generation=record['generation']+1)
            controller._write(locked,record)
        elif record['state']!='ROLLBACK_REQUIRED':
            return result('BLOCK_STATE',record)
        token=record['id'];generation=record['generation']
    # Reacquires the same lock and rejects stale generation or concurrent mutation.
    try:
        sim.rollback(root,token,generation)
    except (OSError,RuntimeError,ValueError):
        return result('BLOCK_RECONCILIATION',controller.inspect(root))
    final=sim.inspect(root)
    if final['recovery']['state']!='ROLLED_BACK' or final['simulatedMonitor']!=sim.BASE:
        return result('BLOCK_VERIFICATION',final['recovery'])
    return result('SIMULATED_ROLLBACK_VERIFIED',final['recovery'])

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root')
    args=parser.parse_args()
    try:
        output=execute(Path(args.root))
    except (OSError,RuntimeError,ValueError,TypeError) as exc:
        output=result('BLOCK_AND_INVESTIGATE')
        output['errorType']=type(exc).__name__
    print(json.dumps(output,indent=2))
    return 0 if output['decision'] in ('SIMULATED_ROLLBACK_VERIFIED','TERMINAL_NO_ACTION') else 2
if __name__=='__main__':sys.exit(main())
