#!/usr/bin/env python3
"""Persistent shared transport multiprocess regression; no physical commands."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
P=HERE/'display-physical-shared-transport-v0600.py'
spec=importlib.util.spec_from_file_location('shared_worker_test',P)
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
ipc=w.ipc
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
results=[]

def check(name,fn):
    try:
        fn();results.append({'scenario':name,'passed':True})
    except Exception as e:
        results.append({'scenario':name,'passed':False,'error':repr(e)})

def setup(topology='internal-only'):
    temp=tempfile.TemporaryDirectory(prefix='serpantinum-shared-')
    root=Path(temp.name);root.chmod(0o700)
    snap=[dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)]
    if topology=='internal-and-hdmi':
        snap.append(dict(name='HDMI-A-1',description='Dell',width=1920,height=1080,refreshRate=60.0,x=1920,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True))
    r=dict(schema=1,id=secrets.token_hex(32),generation=4,state='APPLIED',target='eDP-1',topology=topology,plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1_000_000,phase='APPLY_VERIFIED',execution_backend='physical')
    side=dict(transaction_id=r['id'],boot_id=boot,target='eDP-1',topology=topology,snapshot=snap,snapshot_sha256=w.sha(snap),identity={'instance':'lab_instance'})
    keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    obs=json.loads(json.dumps(snap));obs[0]['refreshRate']=144.06
    with ipc.locked(root) as p:
        ipc.write(p,'transaction.json',r)
        ipc.write(p,'physical-snapshot.json',side)
        ipc.write(p,'physical-watchdog-rehearsal.json',dict(transaction_id=r['id'],transaction_digest=w.sha({k:r[k] for k in keys}),status='ARMED_LAB_ONLY'))
        ipc.write(p,'shared-lab-monitor.json',dict(schema=1,instance='lab_instance',monitors=obs))
    return temp,root

def invoke(root):
    return subprocess.run([sys.executable,'-B',str(P),'recover',str(root)],capture_output=True,text=True,timeout=15)

def scenario(kind):
    temp,root=setup('internal-and-hdmi' if kind=='hdmi' else 'internal-only')
    try:
        if kind in ('normal','hdmi','after-intent','after-effect','concurrent','ignore','identity','hotplug','simulated','corrupt'):
            if kind in ('after-intent','after-effect'):
                outcome=w.recover_lab(root,w.SharedLabTransport(root),failpoint=kind)
                assert outcome['state']=='UNCERTAIN'
                if kind=='after-effect':
                    with ipc.locked(root) as p:
                        assert ipc.read(p,'shared-lab-monitor.json')['monitors'][0]['refreshRate']==60.061
            if kind=='ignore':
                with ipc.locked(root) as p:
                    v=ipc.read(p,'shared-lab-monitor.json');v['ignore_restore']=True;ipc.write(p,'shared-lab-monitor.json',v)
            if kind=='identity':
                with ipc.locked(root) as p:
                    v=ipc.read(p,'shared-lab-monitor.json');v['instance']='other';ipc.write(p,'shared-lab-monitor.json',v)
            if kind=='hotplug':
                with ipc.locked(root) as p:
                    v=ipc.read(p,'shared-lab-monitor.json');v['monitors'].append(dict(v['monitors'][0],name='DP-1'));ipc.write(p,'shared-lab-monitor.json',v)
            if kind=='simulated':
                with ipc.locked(root) as p:
                    v=ipc.read(p,'transaction.json');v['execution_backend']='simulated';ipc.write(p,'transaction.json',v)
            if kind=='corrupt':
                (root/'shared-lab-monitor.json').write_text('{broken')
            if kind=='concurrent':
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    processes=list(pool.map(lambda _:invoke(root),range(4)))
                decisions=[json.loads(x.stdout)['decision'] for x in processes]
                assert decisions.count('LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL')==1,decisions
                assert decisions.count('LAB_TERMINAL_VERIFIED')==3,decisions
            else:
                p=invoke(root);out=json.loads(p.stdout)
                if kind in ('ignore','identity','hotplug','simulated','corrupt'):
                    assert p.returncode!=0 and out['decision'] in ('LAB_RECOVERY_NOT_VERIFIED','BLOCK_AND_INVESTIGATE'),out
                    assert ipc.read(root,'transaction.json')['state']!='ROLLED_BACK'
                else:
                    assert p.returncode==0 and out['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL',out
            if kind not in ('ignore','identity','hotplug','simulated','corrupt'):
                assert ipc.read(root,'transaction.json')['state']=='ROLLED_BACK'
                assert ipc.read(root,'shared-lab-monitor.json')['monitors'][0]['refreshRate']==60.061
                assert invoke(root).returncode==0
        else:raise AssertionError(kind)
    finally:temp.cleanup()

for kind in ('normal','hdmi','after-intent','after-effect','concurrent','ignore','identity','hotplug','simulated','corrupt'):
    check(kind,lambda k=kind:scenario(k))

passed=all(x['passed'] for x in results)
print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False),indent=2))
raise SystemExit(0 if passed else 1)
