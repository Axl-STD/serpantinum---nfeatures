#!/usr/bin/env python3
"""Regression for fail-closed physical/simulated backend separation."""
import importlib.util, json, tempfile, time, secrets
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(file, alias):
    p=HERE/file
    assert p.is_file() and not p.is_symlink()
    s=importlib.util.spec_from_file_location(alias,p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m

recovery=load('display-unified-ipc-recovery-v0600.py','isolation_recovery')
auto=load('display-unified-ipc-autonomous-v0600.py','isolation_auto')
ipc=recovery.ipc
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
results=[]

def check(name, fn):
    try:
        fn(); results.append({'scenario':name,'passed':True})
    except Exception as e:
        results.append({'scenario':name,'passed':False,'error':str(e)})

with tempfile.TemporaryDirectory(prefix='serpantinum-isolation-') as temp:
    for backend in ('physical','unknown','simulated','legacy'):
        for state,phase,mode in (('APPLIED','APPLY_VERIFIED',ipc.TARGET),('UNCERTAIN','ROLLBACK_INTENT',ipc.BASE),('ROLLED_BACK','COMPLETED',ipc.BASE)):
            root=Path(temp)/(backend+'-'+state)
            root.mkdir(mode=0o700)
            record={'schema':1,'id':secrets.token_hex(32),'generation':4,'state':state,'target':'eDP-1','topology':'internal-only','plan_sha256':'b'*64,'boot_id':boot,'deadline_boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1000000000,'phase':phase}
            if backend!='legacy':record['execution_backend']=backend
            with ipc.locked(root) as p:
                ipc.write(p,'transaction.json',record)
                ipc.write(p,'simulated-ipc.json',{'connector':'eDP-1','mode':mode})
            if backend in ('physical','unknown'):
                def denied(root=root,record=record):
                    assert recovery.execute(root)['decision']=='BLOCK_PHYSICAL_TRANSACTION'
                    assert auto.execute(root)['decision']=='BLOCK_PHYSICAL_TRANSACTION'
                    with ipc.locked(root) as p:
                        assert ipc.read(p,'transaction.json')==record
                check(backend+'-'+state+'-failclosed',denied)
                def deny_schedule(root=root):
                    try:auto.schedule(root,'serpantinum-v0600-ipcauto-12345678',runner=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('called')))
                    except ValueError:return
                    raise AssertionError('schedule permitted')
                check(backend+'-'+state+'-schedule-denied',deny_schedule)
            else:
                def legacy(root=root,state=state):
                    decision=recovery.execute(root)['decision']
                    assert decision in ('SIMULATED_ROLLBACK_VERIFIED','SIMULATED_RECONCILIATION_VERIFIED','TERMINAL_VERIFIED'),decision
                check(backend+'-'+state+'-regression',legacy)

out={'selftest':'passed' if all(x['passed'] for x in results) else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(out,indent=2))
raise SystemExit(0 if out['selftest']=='passed' else 2)
