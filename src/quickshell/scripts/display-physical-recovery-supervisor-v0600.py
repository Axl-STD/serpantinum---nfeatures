#!/usr/bin/env python3
"""Independent read-only physical recovery supervisor (systemd/TTY compatible).

Never arms, authorizes, applies, or restores a monitor. Fail closed on stale,
missing or inconsistent evidence. This is a shadow runtime, NOT a watchdog.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent

def dependency(name, alias):
    path=HERE/name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec=importlib.util.spec_from_file_location(alias,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ipc=dependency('display-ipc-transaction-lab-v0600.py','physical_shadow_ipc')
FIELDS=('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')

class Blocked(RuntimeError): pass

def result(decision, **extra):
    return dict(version='0.6.0',mode='physical-recovery-shadow-runtime',decision=decision,
                watchdogPhysicalArmed=False,physicalExecutionEnabled=False,
                executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False,**extra)

def capture(runner=subprocess.run):
    env=os.environ.copy()
    for key in ('WAYLAND_DISPLAY','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE'):
        env.pop(key,None)
    env['XDG_RUNTIME_DIR']='/run/user/'+str(os.getuid())
    def call(args):
        proc=runner(args,capture_output=True,text=True,timeout=8,check=True,env=env)
        return json.loads(proc.stdout)
    instances=call(['hyprctl','instances','-j'])
    if not isinstance(instances,list) or len(instances)!=1 or not isinstance(instances[0],dict):
        raise Blocked('ambiguous compositor')
    instance=instances[0].get('instance')
    if not isinstance(instance,str) or not instance or len(instance)>160 or not all(c.isalnum() or c in '_-' for c in instance):
        raise Blocked('invalid compositor identity')
    monitors=call(['hyprctl','-i',instance,'monitors','-j'])
    return instance,monitors

def inspect(root, runner=subprocess.run):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
        s=ipc.read(p,'physical-snapshot.json')
        receipt=ipc.read(p,'physical-watchdog-rehearsal.json')
        if not isinstance(r,dict) or r.get('execution_backend')!='physical':
            raise Blocked('invalid backend')
        if r.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            raise Blocked('boot drift')
        if not isinstance(s,dict) or any(s.get(k)!=r.get(k) for k in ('boot_id','target','topology')) or s.get('transaction_id')!=r.get('id'):
            raise Blocked('snapshot identity')
        saved=s.get('snapshot')
        if not isinstance(saved,list) or s.get('snapshot_sha256')!=hashlib.sha256(ipc.canonical(saved)).hexdigest():
            raise Blocked('snapshot integrity')
        expected={r.get('target')} if r.get('topology')=='internal-only' else {r.get('target'),'HDMI-A-1'} if r.get('topology')=='internal-and-hdmi' else set()
        if not expected or len(saved)!=len(expected) or any(not isinstance(m,dict) or any(k not in m for k in FIELDS) for m in saved) or {m['name'] for m in saved}!=expected:
            raise Blocked('snapshot topology')
        if not isinstance(receipt,dict) or receipt.get('transaction_id')!=r.get('id'):
            raise Blocked('obligation identity')
        keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
        if any(k not in r for k in keys) or receipt.get('transaction_digest')!=hashlib.sha256(ipc.canonical({k:r[k] for k in keys})).hexdigest():
            raise Blocked('obligation digest')
        # Do not allow unknown receipt formats to masquerade as armed.
        if receipt.get('status')!='ARMED_LAB_ONLY':
            raise Blocked('unsupported physical obligation')
        identity=s.get('identity')
        if not isinstance(identity,dict) or not isinstance(identity.get('instance'),str):
            raise Blocked('missing compositor identity')
        token=(r.get('id'),r.get('generation'),r.get('state'),r.get('phase'))
        baseline={m['name']:m for m in saved}
        expected_instance=identity['instance']
    # Never hold IPC lock across external compositor calls.
    instance,monitors=capture(runner)
    if instance!=expected_instance:
        raise Blocked('compositor changed')
    if not isinstance(monitors,list) or len(monitors)!=len(expected) or any(not isinstance(m,dict) for m in monitors):
        raise Blocked('topology changed')
    current={m.get('name'):m for m in monitors}
    if len(current)!=len(monitors) or set(current)!=expected:
        raise Blocked('connector changed')
    for name in expected:
        if any(current[name].get(k)!=baseline[name][k] for k in FIELDS if k!='refreshRate'):
            raise Blocked('monitor properties changed')
        hz=current[name].get('refreshRate')
        if type(hz) not in (float,int):
            raise Blocked('invalid refresh')
        if name!=r['target'] and abs(hz-baseline[name]['refreshRate'])>0.02:
            raise Blocked('external refresh changed')
        if name==r['target'] and min(abs(hz-baseline[name]['refreshRate']),abs(hz-144.06))>0.02:
            raise Blocked('unexpected target refresh')
    # Re-read under lock: observation may have raced with another worker.
    with ipc.locked(root) as p:
        after=ipc.read(p,'transaction.json')
        if (after.get('id'),after.get('generation'),after.get('state'),after.get('phase'))!=token:
            raise Blocked('concurrent transaction change')
        current_snapshot=ipc.read(p,'physical-snapshot.json')
        if current_snapshot!=s:
            raise Blocked('concurrent snapshot change')
        if ipc.read(p,'physical-watchdog-rehearsal.json')!=receipt:
            raise Blocked('concurrent obligation change')
    return result('READONLY_PHYSICAL_OBLIGATION_OBSERVED',transactionId=token[0],generation=token[1],state=token[2],phase=token[3],target=r['target'],observedMode='ORIGINAL' if abs(current[r['target']]['refreshRate']-baseline[r['target']]['refreshRate'])<=0.02 else 'CANDIDATE',obligation='LAB_ONLY_NOT_PHYSICAL')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inspect','watch'))
    parser.add_argument('root',type=Path)
    parser.add_argument('--iterations',type=int,default=1)
    parser.add_argument('--interval',type=float,default=1.0)
    a=parser.parse_args()
    if not 1<=a.iterations<=300 or not 0.1<=a.interval<=10:
        print(json.dumps(result('BLOCK_ARGUMENTS')));return 2
    for i in range(a.iterations if a.action=='watch' else 1):
        try:
            value=inspect(a.root)
        except (Blocked,ipc.Denied,OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError,RuntimeError) as exc:
            value=result('BLOCK_AND_INVESTIGATE',errorType=type(exc).__name__)
        print(json.dumps(value),flush=True)
        if value['decision']!='READONLY_PHYSICAL_OBLIGATION_OBSERVED':return 2
        if a.action=='watch' and i+1<a.iterations:time.sleep(a.interval)
    return 0

if __name__=='__main__':raise SystemExit(main())
