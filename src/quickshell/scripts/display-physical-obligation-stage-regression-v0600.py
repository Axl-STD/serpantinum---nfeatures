#!/usr/bin/env python3
"""Offline regression for the staging boundary; never touches Hyprland."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
p=HERE/'display-physical-obligation-stage-v0600.py'
spec=importlib.util.spec_from_file_location('stage_test',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture():
    tmp=tempfile.TemporaryDirectory(prefix='serpantinum-stage-')
    root=Path(tmp.name);root.chmod(0o700)
    base=dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    r=dict(schema=1,id=secrets.token_hex(32),generation=2,state='ARMED',phase='NONE',execution_backend='physical',target='eDP-1',topology='internal-only',plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+60_000_000_000)
    s=dict(transaction_id=r['id'],boot_id=boot,target='eDP-1',topology='internal-only',snapshot=[base],snapshot_sha256=m.digest([base]),identity={'instance':'lab_instance'})
    with m.ipc.locked(root) as path:
        m.ipc.write(path,'transaction.json',r)
        m.ipc.write(path,'physical-snapshot.json',s)
    return tmp,root

def mutate(root,filename,fn):
    with m.ipc.locked(root) as p:
        d=m.ipc.read(p,filename);fn(d);m.ipc.write(p,filename,d)

def blocked(fn):
    try:fn()
    except (m.Blocked,m.ipc.Denied,OSError,ValueError,KeyError,TypeError):return True
    return False

def test_case(label,fn):
    tmp,root=fixture()
    try:
        passed=fn(root)
        return dict(scenario=label,passed=bool(passed))
    finally:tmp.cleanup()

cases=[
 ('stage-and-inspect',lambda r: m.stage(r)['decision']=='PHYSICAL_OBLIGATION_STAGED_NOT_ARMED' and m.inspect(r)['decision']=='PHYSICAL_OBLIGATION_STAGED_VERIFIED_NOT_ARMED'),
 ('duplicate-stage',lambda r: (m.stage(r),blocked(lambda:m.stage(r)))[1]),
 ('missing-manifest',lambda r: blocked(lambda:m.inspect(r))),
 ('bad-backend',lambda r: (mutate(r,'transaction.json',lambda x:x.update(execution_backend='simulation')),blocked(lambda:m.stage(r)))[1]),
 ('expired',lambda r: (mutate(r,'transaction.json',lambda x:x.update(deadline_boottime_ns=1)),blocked(lambda:m.stage(r)))[1]),
 ('wrong-boot',lambda r: (mutate(r,'transaction.json',lambda x:x.update(boot_id='other')),blocked(lambda:m.stage(r)))[1]),
 ('wrong-phase',lambda r: (mutate(r,'transaction.json',lambda x:x.update(phase='APPLY_VERIFIED')),blocked(lambda:m.stage(r)))[1]),
 ('wrong-state',lambda r: (mutate(r,'transaction.json',lambda x:x.update(state='APPLIED')),blocked(lambda:m.stage(r)))[1]),
 ('snapshot-tamper',lambda r: (mutate(r,'physical-snapshot.json',lambda x:x['snapshot'][0].update(refreshRate=144.06)),blocked(lambda:m.stage(r)))[1]),
 ('instance-missing',lambda r: (mutate(r,'physical-snapshot.json',lambda x:x.update(identity={})),blocked(lambda:m.stage(r)))[1]),
 ('manifest-tamper',lambda r: (m.stage(r),mutate(r,m.NAME,lambda x:x.update(status='ARMED_PHYSICAL')),blocked(lambda:m.inspect(r)))[2]),
 ('transaction-tamper',lambda r: (m.stage(r),mutate(r,'transaction.json',lambda x:x.update(plan_sha256='c'*64)),blocked(lambda:m.inspect(r)))[2]),
 ('snapshot-after-stage',lambda r: (m.stage(r),mutate(r,'physical-snapshot.json',lambda x:x.update(identity={'instance':'different'})),blocked(lambda:m.inspect(r)))[2]),
 ('lab-receipt-present',lambda r: (mutate_receipt(r),blocked(lambda:m.stage(r)))[1]),
 ('concurrent-stage',lambda r: concurrent_stage(r)),
]
def mutate_receipt(root):
    with m.ipc.locked(root) as p:m.ipc.write(p,'physical-watchdog-rehearsal.json',{'status':'ARMED_LAB_ONLY'})
def concurrent_stage(root):
    def run(_):
        try:m.stage(root);return True
        except m.Blocked:return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results=list(ex.map(run,range(6)))
    return sum(results)==1 and m.inspect(root)['decision']=='PHYSICAL_OBLIGATION_STAGED_VERIFIED_NOT_ARMED'

if __name__=='__main__':
    results=[test_case(name,fn) for name,fn in cases]
    output=dict(selftest='passed' if all(x['passed'] for x in results) else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False)
    print(json.dumps(output,indent=2))
    sys.exit(0 if output['selftest']=='passed' else 2)
