#!/usr/bin/env python3
"""Independent physical-obligation observer. Never issues monitor writes or authorization."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
P=HERE/'display-ipc-transaction-lab-v0600.py'
if P.is_symlink() or not P.is_file(): raise RuntimeError('unsafe IPC dependency')
spec=importlib.util.spec_from_file_location('observer_ipc',P)
ipc=importlib.util.module_from_spec(spec);spec.loader.exec_module(ipc)

class Denied(RuntimeError): pass

def output(decision, **kw):
    return dict(version='0.6.0',mode='physical-supervisor-observer',decision=decision,
                watchdogPhysicalArmed=False,physicalExecutionEnabled=False,
                executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False,**kw)

def inspect(root, probe=None):
    with ipc.locked(root) as p:
        record=ipc.read(p,'transaction.json')
        snap=ipc.read(p,'physical-snapshot.json')
        if record.get('execution_backend')!='physical': raise Denied('wrong backend')
        if record.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip(): raise Denied('boot drift')
        if snap.get('transaction_id')!=record.get('id') or snap.get('boot_id')!=record.get('boot_id'): raise Denied('snapshot identity')
        if not isinstance(snap.get('snapshot'),list) or snap.get('snapshot_sha256')!=__import__('hashlib').sha256(ipc.canonical(snap['snapshot'])).hexdigest(): raise Denied('snapshot digest')
        receipt=ipc.read(p,'physical-watchdog-rehearsal.json')
        if receipt.get('status')=='ARMED_LAB_ONLY':
            return output('LAB_OBLIGATION_NOT_PHYSICAL',state=record.get('state'),generation=record.get('generation'))
        # An unknown receipt is never sufficient for physical arming.
        return output('BLOCK_UNSUPPORTED_PHYSICAL_OBLIGATION',state=record.get('state'))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inspect','watch'))
    parser.add_argument('root',type=Path)
    parser.add_argument('--interval',type=float,default=1)
    parser.add_argument('--iterations',type=int,default=1)
    args=parser.parse_args()
    if args.iterations<1 or args.iterations>300 or not 0.1<=args.interval<=10:
        print(json.dumps(output('BLOCK_ARGUMENTS')));return 2
    try:
        for n in range(args.iterations if args.action=='watch' else 1):
            r=inspect(args.root)
            print(json.dumps(r),flush=True)
            if r['decision']!='LAB_OBLIGATION_NOT_PHYSICAL':return 2
            if args.action=='watch' and n+1<args.iterations:time.sleep(args.interval)
        return 0
    except (OSError,ValueError,TypeError,KeyError,Denied,ipc.Denied) as e:
        print(json.dumps(output('BLOCK_AND_INVESTIGATE',errorType=type(e).__name__)),flush=True)
        return 2
if __name__=='__main__':raise SystemExit(main())
