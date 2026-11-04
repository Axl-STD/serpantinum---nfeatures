#!/usr/bin/env python3
"""Offline regression for independent read-only physical recovery supervisor."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

HERE=Path(__file__).resolve().parent
P=HERE/'display-physical-recovery-supervisor-v0600.py'
s=importlib.util.spec_from_file_location('shadow_supervisor',P)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

base=dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
instance='lab_instance_123456'

def runner(monitors=None, instances=None, fail=False, mutate=None):
    def call(args,**kw):
        assert args[0]=='hyprctl' and args[1] in ('instances','-i')
        assert 'WAYLAND_DISPLAY' not in kw['env'] and 'DISPLAY' not in kw['env']
        if fail:raise subprocess.TimeoutExpired(args,8)
        if mutate:mutate(args)
        return subprocess.CompletedProcess(args,0,json.dumps(([{'instance':instance}] if instances is None else instances) if args[1]=='instances' else ([dict(base)] if monitors is None else monitors)))
    return call

def setup(root):
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    r=dict(schema=1,id=secrets.token_hex(32),generation=4,state='ROLLBACK_REQUIRED',phase='APPLY_VERIFIED',execution_backend='physical',target='eDP-1',topology='internal-only',plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1_000_000_000)
    snap=dict(transaction_id=r['id'],boot_id=boot,target='eDP-1',topology='internal-only',snapshot=[dict(base)],snapshot_sha256=m.ipc.canonical([base]),identity={'instance':instance})
    import hashlib
    snap['snapshot_sha256']=hashlib.sha256(snap['snapshot_sha256']).hexdigest()
    with m.ipc.locked(root) as p:
        m.ipc.write(p,'transaction.json',r)
        m.ipc.write(p,'physical-snapshot.json',snap)
        keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
        m.ipc.write(p,'physical-watchdog-rehearsal.json',dict(transaction_id=r['id'],transaction_digest=hashlib.sha256(m.ipc.canonical({k:r[k] for k in keys})).hexdigest(),status='ARMED_LAB_ONLY'))
    return r

def check(label,fn,expected):
    try:
        v=fn()
        passed=(v==expected)
    except Exception as e:
        passed=(expected=='BLOCK' and isinstance(e,(m.Blocked,m.ipc.Denied,OSError,ValueError,subprocess.SubprocessError)))
    return dict(scenario=label,passed=passed)

def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix='serpantinum-shadow-runtime-') as tmp:
        root=Path(tmp)/'transaction';root.mkdir(mode=0o700)
        r=setup(root)
        def decision(call):return call()['decision']
        results.append(check('original',lambda:decision(lambda:m.inspect(root,runner())),'READONLY_PHYSICAL_OBLIGATION_OBSERVED'))
        results.append(check('candidate',lambda:m.inspect(root,runner(monitors=[dict(base,refreshRate=144.06)]))['observedMode'],'CANDIDATE'))
        results.append(check('hotplug',lambda:m.inspect(root,runner(monitors=[base,dict(base,name='HDMI-A-1')])),'BLOCK'))
        results.append(check('geometry',lambda:m.inspect(root,runner(monitors=[dict(base,x=10)])),'BLOCK'))
        results.append(check('refresh',lambda:m.inspect(root,runner(monitors=[dict(base,refreshRate=75)])),'BLOCK'))
        results.append(check('instances',lambda:m.inspect(root,runner(instances=[])),'BLOCK'))
        results.append(check('ipc_timeout',lambda:m.inspect(root,runner(fail=True)),'BLOCK'))
        def change(args):
            if args[1]=='-i':
                with m.ipc.locked(root) as p:
                    rr=m.ipc.read(p,'transaction.json');rr['generation']+=1;m.ipc.write(p,'transaction.json',rr)
        results.append(check('race',lambda:m.inspect(root,runner(mutate=change)),'BLOCK'))
        with m.ipc.locked(root) as p:
            rr=m.ipc.read(p,'transaction.json');rr['generation']=4;m.ipc.write(p,'transaction.json',rr)
            receipt=m.ipc.read(p,'physical-watchdog-rehearsal.json');receipt['status']='ARMED_PHYSICAL';m.ipc.write(p,'physical-watchdog-rehearsal.json',receipt)
        results.append(check('physical_receipt_rejected',lambda:m.inspect(root,runner()),'BLOCK'))
        with m.ipc.locked(root) as p:
            receipt=m.ipc.read(p,'physical-watchdog-rehearsal.json');receipt['status']='ARMED_LAB_ONLY';m.ipc.write(p,'physical-watchdog-rehearsal.json',receipt)
            snap=m.ipc.read(p,'physical-snapshot.json');snap['snapshot_sha256']='0'*64;m.ipc.write(p,'physical-snapshot.json',snap)
        results.append(check('snapshot_corrupt',lambda:m.inspect(root,runner()),'BLOCK'))
    passed=all(x['passed'] for x in results)
    print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False),indent=2))
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
