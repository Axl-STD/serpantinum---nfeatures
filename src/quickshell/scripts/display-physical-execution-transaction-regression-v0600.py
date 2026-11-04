#!/usr/bin/env python3
"""Consolidated offline state-machine and fail-closed regression tests."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time

HERE = Path(__file__).resolve().parent
P = HERE / 'display-physical-execution-transaction-v0600.py'
spec = importlib.util.spec_from_file_location('physical_execution_rehearsal', P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def fixture():
    td = tempfile.TemporaryDirectory(prefix='serpantinum-exec-tx-')
    root = Path(td.name)
    root.chmod(0o700)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    base = dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,
                refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    r = dict(schema=1,id=secrets.token_hex(32),generation=2,state='ARMED',phase='NONE',
             execution_backend='physical',target='eDP-1',topology='internal-only',
             plan_sha256='b'*64,boot_id=boot,
             deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+120_000_000_000)
    s = dict(transaction_id=r['id'],boot_id=boot,target='eDP-1',topology='internal-only',
             snapshot=[base],snapshot_sha256=m.stage.digest([base]),identity={'instance':'lab_instance'})
    with m.ipc.locked(root) as p:
        m.ipc.write(p,'transaction.json',r)
        m.ipc.write(p,'physical-snapshot.json',s)
    assert m.stage.stage(root)['decision']=='PHYSICAL_OBLIGATION_STAGED_NOT_ARMED'
    return td, root

def blocked(fn):
    try: fn()
    except (m.Blocked,m.stage.Blocked,m.ipc.Denied,OSError,ValueError,KeyError,TypeError): return True
    return False

def change(root, name, fn):
    with m.ipc.locked(root) as p:
        v=m.ipc.read(p,name)
        m.ipc.write(p,name,fn(v))

def run():
    results=[]
    def test(name, fn):
        try: passed=bool(fn())
        except Exception: passed=False
        results.append(dict(scenario=name,passed=passed))
    td,root=fixture()
    try:
        test('prepare',lambda:m.execute(root,'prepare')['decision']=='REHEARSAL_PREPARED_NOT_ARMED')
        test('duplicate',lambda:blocked(lambda:m.execute(root,'prepare')))
        test('inspect',lambda:m.execute(root,'inspect')['step']==0)
        for i in range(1,5):
            test('transition-'+str(i),lambda i=i:m.execute(root,'advance')['step']==i)
        test('terminal-idempotent',lambda:m.execute(root,'advance')['decision']=='REHEARSAL_TERMINAL_NOT_PHYSICAL')
        test('terminal-inspect',lambda:m.execute(root,'inspect')['phase']=='ROLLBACK_OBSERVED_SIMULATED')
        with m.ipc.locked(root) as p:
            original=m.ipc.read(p,'transaction.json')
        test('no-authority-transition',lambda:all(m.execute(root,'inspect')[k] is False for k in ('physicalExecutionEnabled','executionAuthorized','rollbackPhysicalVerified','watchdogPhysicalArmed')))
        test('original-transaction-unchanged',lambda:m.ipc.read(root,'transaction.json')==original)
    finally: td.cleanup()
    for name,filename,modify in [
        ('generation-drift','transaction.json',lambda v:dict(v,generation=v['generation']+1)),
        ('identity-drift','transaction.json',lambda v:dict(v,id=secrets.token_hex(32))),
        ('expired','transaction.json',lambda v:dict(v,deadline_boottime_ns=1)),
        ('state-drift','transaction.json',lambda v:dict(v,state='APPLIED')),
        ('snapshot-corrupt','physical-snapshot.json',lambda v:dict(v,snapshot_sha256='0'*64)),
        ('manifest-corrupt',m.stage.NAME,lambda v:dict(v,instance='other')),
        ('rehearsal-corrupt',m.NAME,lambda v:dict(v,phase='INVALID')),
    ]:
        td,root=fixture()
        try:
            m.execute(root,'prepare')
            change(root,filename,modify)
            test(name,lambda root=root:blocked(lambda:m.execute(root,'advance')))
        finally: td.cleanup()
    td,root=fixture()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures=[pool.submit(lambda: (m.execute(root,'prepare')['decision']=='REHEARSAL_PREPARED_NOT_ARMED')) for _ in range(8)]
            count=sum(f.result() for f in futures if not f.exception())
        test('concurrent-single-prepare',lambda:count==1)
        test('concurrent-record-valid',lambda:m.execute(root,'inspect')['step']==0)
    finally: td.cleanup()
    passed=all(x['passed'] for x in results)
    print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(results),results=results,
                          physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,
                          rollbackPhysicalVerified=False),indent=2))
    return 0 if passed else 2

if __name__=='__main__': raise SystemExit(run())
