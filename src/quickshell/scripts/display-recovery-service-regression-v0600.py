#!/usr/bin/env python3
"""Service regression using isolated persistent lab fixtures, no Hyprland writes."""
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('recovery_service', HERE/'display-recovery-service-v0600.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
shared = service.lab.shared
ipc = shared.ipc


def fixture(root, backend='physical', receipt='ARMED_LAB_ONLY', state='ROLLBACK_REQUIRED', instance='lab_instance', hz=144.06):
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    original=dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    record=dict(schema=1,id=secrets.token_hex(32),generation=4,state=state,phase='APPLY_VERIFIED' if state!='ARMED' else 'NONE',execution_backend=backend,target='eDP-1',topology='internal-only',plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1000000000)
    snapshot=dict(transaction_id=record['id'],boot_id=boot,target='eDP-1',topology='internal-only',snapshot=[original],snapshot_sha256=shared.sha([original]),identity={'instance':'lab_instance'})
    keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    obligation=dict(transaction_id=record['id'],transaction_digest=shared.sha({k:record[k] for k in keys}),status=receipt)
    with ipc.locked(root) as p:
        ipc.write(p,'transaction.json',record)
        ipc.write(p,'physical-snapshot.json',snapshot)
        ipc.write(p,'physical-watchdog-rehearsal.json',obligation)
        ipc.write(p,'shared-lab-monitor.json',dict(schema=1,instance=instance,monitors=[dict(original,refreshRate=hz)]))

def mutate(root,filename,key,value):
    with ipc.locked(root) as p:
        obj=ipc.read(p,filename);obj[key]=value;ipc.write(p,filename,obj)

results=[]
def check(name, callback):
    try:
        with tempfile.TemporaryDirectory(prefix='serpantinum-service-') as d:
            root=Path(d);root.chmod(0o700)
            callback(root)
        results.append(dict(scenario=name,passed=True))
    except Exception as exc:
        results.append(dict(scenario=name,passed=False,error=type(exc).__name__+': '+str(exc)))


def success(root):
    fixture(root)
    assert service.inspect(root)['decision']=='LAB_OBLIGATION_OBSERVED'
    assert service.recover(root)['decision']=='LAB_RECOVERY_VERIFIED_NOT_PHYSICAL'
    assert service.inspect(root)['state']=='ROLLED_BACK'
    assert service.recover(root)['decision']=='LAB_RECOVERY_VERIFIED_NOT_PHYSICAL'


def denied(root, change):
    fixture(root)
    change(root)
    try: service.inspect(root)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError): return
    raise AssertionError('unsafe fixture accepted')

check('recovery-and-terminal-idempotence',success)
for name,field,value in [('simulated-backend','execution_backend','simulated'),('new-boot','boot_id','invalid'),('wrong-target','target','DP-1'),('wrong-topology','topology','internal-and-hdmi')]:
    def scenario(root,field=field,value=value):
        fixture(root)
        with ipc.locked(root) as p:
            x=ipc.read(p,'transaction.json');x[field]=value;ipc.write(p,'transaction.json',x)
        try:service.inspect(root)
        except (RuntimeError,ValueError,OSError,KeyError,TypeError):return
        raise AssertionError('accepted')
    check(name,scenario)
for name,receipt in [('lab-receipt-replaced','ARMED_PHYSICAL'),('receipt-revoked','REVOKED')]:
    check(name,lambda root,receipt=receipt:denied(root,lambda r: mutate(r,'physical-watchdog-rehearsal.json','status',receipt)))

check('compositor-identity-drift',lambda r:denied(r,lambda p:mutate(p,'shared-lab-monitor.json','instance','other')))
check('corrupt-shared-state',lambda r:denied(r,lambda p:(p/'shared-lab-monitor.json').write_text('{invalid')))
check('snapshot-digest-drift',lambda r:denied(r,lambda p:mutate(p,'physical-snapshot.json','snapshot_sha256','0'*64)))
check('missing-snapshot',lambda r:denied(r,lambda p:(p/'physical-snapshot.json').unlink()))
ok=all(x['passed'] for x in results)
print(json.dumps(dict(selftest='passed' if ok else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False),indent=2))
raise SystemExit(0 if ok else 2)
