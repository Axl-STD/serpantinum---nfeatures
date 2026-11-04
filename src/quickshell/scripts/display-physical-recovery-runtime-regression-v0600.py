#!/usr/bin/env python3
"""Isolated offline matrix for physical recovery runtime boundary."""
import copy
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time

HERE = Path(__file__).resolve().parent
p = HERE / 'display-physical-recovery-runtime-v0600.py'
spec = importlib.util.spec_from_file_location('runtime_test', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = dict(name='eDP-1',description='AU Optronics B156HAN15.H',width=1920,height=1080,
            refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
HDMI = dict(name='HDMI-A-1',description='Dell Inc. Inspiron 3043',width=1920,height=1080,
            refreshRate=60.0,x=1920,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
checks=[]
def check(label, condition):
    assert condition, label
    checks.append(dict(scenario=label, passed=True))

def fixture(root, target='eDP-1', hdmi=False, state='APPLIED', phase='APPLY_VERIFIED'):
    b = copy.deepcopy(BASE); b['name'] = target
    baseline = [b] + ([copy.deepcopy(HDMI)] if hdmi else [])
    baseline.sort(key=lambda x:x['name'])
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    r = dict(schema=1,id=secrets.token_hex(32),generation=4,state=state,phase=phase,
             target=target,topology='internal-and-hdmi' if hdmi else 'internal-only',
             boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+10**10,
             plan_sha256='b'*64)
    s = dict(schema=1,transaction_id=r['id'],generation_at_binding=1,boot_id=boot,
             target=target,topology=r['topology'],snapshot=baseline,
             snapshot_sha256=m.snapshot.digest(baseline),identity=dict(instance='fake-instance-123'))
    w = dict(schema=1,transaction_id=r['id'],generation_at_arm=2,
             transaction_digest=m.obligation.digest(r),deadline_boottime_ns=r['deadline_boottime_ns'],
             boot_id=boot,status='ARMED_SIMULATED')
    with m.ipc.locked(root) as p:
        m.ipc.write(p,'transaction.json',r)
        m.ipc.write(p,'physical-snapshot.json',s)
        m.ipc.write(p,'watchdog-obligation.json',w)
    return baseline

with tempfile.TemporaryDirectory(prefix='serpantinum-runtime-') as tmp:
    for connector in ('eDP-1','eDP-2'):
        for hdmi in (False, True):
            root = Path(tmp) / (connector + str(hdmi)); root.mkdir(mode=0o700)
            base = fixture(root, connector, hdmi)
            candidate = copy.deepcopy(base)
            next(x for x in candidate if x['name']==connector)['refreshRate']=144.06
            check(f'{connector}_{hdmi}_candidate',m.inspect(root,'fake-instance-123',lambda:candidate)['decision']=='CANDIDATE_RECOVERY_REQUIRED')
            check(f'{connector}_{hdmi}_original',m.inspect(root,'fake-instance-123',lambda:base)['decision']=='ORIGINAL_PRESENT_PENDING_RECONCILIATION')
            fake = copy.deepcopy(candidate)
            def restore(target, baseline):
                fake[:] = copy.deepcopy(baseline)
            check(f'{connector}_{hdmi}_rehearsal',m.rehearse(root,'fake-instance-123',lambda:fake,restore)['decision']=='SIMULATED_RESTORE_READBACK_PASS_NOT_COMMITTED')
            check(f'{connector}_{hdmi}_no_transaction_write',m.ipc.read(root,'transaction.json')['state']=='APPLIED')
            check(f'{connector}_{hdmi}_no_physical_authority',m.inspect(root,'fake-instance-123',lambda:base)['commandsExecuted']==0)
            fake = copy.deepcopy(candidate)
            check(f'{connector}_{hdmi}_ignored_restore',m.rehearse(root,'fake-instance-123',lambda:fake,lambda *_:None)['decision']=='BLOCK_RESTORE_READBACK')
            for key, value in [('x',5),('scale',1.25),('transform',1),('width',1280)]:
                changed=copy.deepcopy(base)
                next(x for x in changed if x['name']==connector)[key]=value
                check(f'{connector}_{hdmi}_{key}_drift',m.inspect(root,'fake-instance-123',lambda:changed)['decision']=='BLOCK_MONITOR_DRIFT')
            changed=copy.deepcopy(base)
            next(x for x in changed if x['name']==connector)['refreshRate']=75
            check(f'{connector}_{hdmi}_unknown_rate',m.inspect(root,'fake-instance-123',lambda:changed)['decision']=='BLOCK_UNEXPECTED_MODE')
            check(f'{connector}_{hdmi}_topology',m.inspect(root,'fake-instance-123',lambda:[])['decision']=='BLOCK_TOPOLOGY_CHANGED')
            with m.ipc.locked(root) as p:
                s=m.ipc.read(p,'physical-snapshot.json'); s['snapshot_sha256']='0'*64
                m.ipc.write(p,'physical-snapshot.json',s)
            try:
                m.inspect(root,'fake-instance-123',lambda:base)
                bad=False
            except (RuntimeError,ValueError,KeyError):
                bad=True
            check(f'{connector}_{hdmi}_tamper_rejected',bad)

print(json.dumps(dict(selftest='passed',totalScenarios=len(checks),results=checks,
                      physicalExecutionEnabled=False,commandsExecuted=0),indent=2))
