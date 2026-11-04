#!/usr/bin/env python3
"""Stage and inspect a physical recovery obligation, NEVER arm or execute it.

This is a fail-closed staging boundary. No systemd scheduling and no hyprctl
writes. A staged receipt is NOT accepted by the laboratory recovery worker.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
DEP = HERE / 'display-ipc-transaction-lab-v0600.py'
if DEP.is_symlink() or not DEP.is_file():
    raise RuntimeError('unsafe IPC dependency')
spec = importlib.util.spec_from_file_location('obligation_stage_ipc', DEP)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)

NAME = 'physical-obligation-staged.json'
FIELDS = ('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')
KEYS = ('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
class Blocked(RuntimeError): pass

def digest(value):
    return hashlib.sha256(ipc.canonical(value)).hexdigest()

def response(decision, **extra):
    return dict(version='0.6.0', mode='physical-obligation-stage-only', decision=decision,
                watchdogPhysicalArmed=False, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **extra)

def validate(p):
    r = ipc.read(p, 'transaction.json')
    s = ipc.read(p, 'physical-snapshot.json')
    if not isinstance(r,dict) or r.get('execution_backend') != 'physical':
        raise Blocked('backend')
    if r.get('schema') != 1 or r.get('target') not in ('eDP-1','eDP-2'):
        raise Blocked('schema/target')
    if any(k not in r for k in KEYS) or not isinstance(r.get('id'),str):
        raise Blocked('transaction identity')
    if r.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise Blocked('boot drift')
    if type(r.get('deadline_boottime_ns')) is not int:
        raise Blocked('deadline')
    if not isinstance(s,dict) or s.get('transaction_id') != r['id'] or any(s.get(k)!=r.get(k) for k in ('boot_id','target','topology')):
        raise Blocked('snapshot identity')
    monitors=s.get('snapshot')
    expected={r['target']} if r['topology']=='internal-only' else {r['target'],'HDMI-A-1'} if r['topology']=='internal-and-hdmi' else set()
    if not expected or not isinstance(monitors,list) or len(monitors)!=len(expected) or not all(isinstance(m,dict) and all(k in m for k in FIELDS) for m in monitors):
        raise Blocked('snapshot shape')
    if {m['name'] for m in monitors}!=expected or s.get('snapshot_sha256')!=digest(monitors):
        raise Blocked('snapshot integrity')
    identity=s.get('identity')
    if not isinstance(identity,dict) or not isinstance(identity.get('instance'),str) or not identity['instance']:
        raise Blocked('instance')
    return r,s

def manifest(r,s):
    return dict(schema=1, status='STAGED_NOT_ARMED', transaction_id=r['id'],
                transaction_digest=digest({k:r[k] for k in KEYS}),
                snapshot_sha256=s['snapshot_sha256'],
                instance=s['identity']['instance'], boot_id=r['boot_id'],
                target=r['target'], topology=r['topology'],
                deadline_boottime_ns=r['deadline_boottime_ns'])

def stage(root):
    with ipc.locked(root) as p:
        r,s=validate(p)
        if r.get('state')!='ARMED' or r.get('phase')!='NONE' or type(r.get('generation')) is not int:
            raise Blocked('not pre-effect armed')
        if time.clock_gettime_ns(time.CLOCK_BOOTTIME)>=r['deadline_boottime_ns']:
            raise Blocked('expired')
        if (p/NAME).exists() or (p/'physical-watchdog-rehearsal.json').exists():
            raise Blocked('existing obligation')
        ipc.write(p,NAME,manifest(r,s))
        return response('PHYSICAL_OBLIGATION_STAGED_NOT_ARMED',transactionId=r['id'])

def inspect(root):
    with ipc.locked(root) as p:
        r,s=validate(p)
        m=ipc.read(p,NAME)
        if not isinstance(m,dict) or m!=manifest(r,s):
            raise Blocked('obligation changed')
        return response('PHYSICAL_OBLIGATION_STAGED_VERIFIED_NOT_ARMED',transactionId=r['id'],state=r.get('state'),phase=r.get('phase'))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=('stage','inspect'))
    ap.add_argument('root')
    args=ap.parse_args()
    try:
        value=stage(args.root) if args.action=='stage' else inspect(args.root)
        code=0
    except (Blocked,ipc.Denied,OSError,ValueError,KeyError,TypeError) as exc:
        value=response('BLOCK_AND_INVESTIGATE',errorType=type(exc).__name__)
        code=2
    print(json.dumps(value,indent=2))
    return code

if __name__=='__main__':raise SystemExit(main())
