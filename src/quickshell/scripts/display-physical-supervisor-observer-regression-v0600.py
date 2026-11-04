#!/usr/bin/env python3
"""Fail-closed observer regression; does not interact with Hyprland."""
import importlib.util,json,tempfile,secrets,sys
from pathlib import Path
P=Path(__file__).with_name('display-physical-supervisor-observer-v0600.py')
s=importlib.util.spec_from_file_location('obs',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
checks=[]
def check(name,func):
    try:func();checks.append(dict(scenario=name,passed=True))
    except Exception as e:checks.append(dict(scenario=name,passed=False,error=str(e)))
with tempfile.TemporaryDirectory(prefix='serpantinum-observer-') as td:
    root=Path(td);root.chmod(0o700)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    monitor=dict(name='eDP-1',refreshRate=60.061)
    r=dict(id=secrets.token_hex(32),boot_id=boot,execution_backend='physical',state='ARMED',generation=1)
    snap=dict(transaction_id=r['id'],boot_id=boot,snapshot=[monitor],snapshot_sha256=__import__('hashlib').sha256(m.ipc.canonical([monitor])).hexdigest())
    receipt=dict(status='ARMED_LAB_ONLY')
    def write():
        with m.ipc.locked(root) as p:
            for n,v in [('transaction.json',r),('physical-snapshot.json',snap),('physical-watchdog-rehearsal.json',receipt)]:m.ipc.write(p,n,v)
    write()
    check('lab-not-physical',lambda: (_ for _ in ()).throw(AssertionError()) if m.inspect(root)['decision']!='LAB_OBLIGATION_NOT_PHYSICAL' else None)
    for i in range(1,31):
        def case(i=i):
            old=dict(r);old_snap=dict(snap);old_receipt=dict(receipt)
            try:
                if i%5==0:r['execution_backend']='simulated'
                elif i%5==1:r['boot_id']='wrong'
                elif i%5==2:snap['transaction_id']='wrong'
                elif i%5==3:snap['snapshot_sha256']='0'*64
                else:receipt['status']='ARMED_PHYSICAL'
                write()
                try:result=m.inspect(root)['decision']
                except (m.Denied,m.ipc.Denied):return
                assert result not in ('LAB_OBLIGATION_NOT_PHYSICAL','PHYSICAL_ARMED')
            finally:r.clear();r.update(old);snap.clear();snap.update(old_snap);receipt.clear();receipt.update(old_receipt);write()
        check('reject-%02d'%i,case)
passed=all(x['passed'] for x in checks)
print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(checks),results=checks,physicalExecutionEnabled=False,commandsExecuted=0),indent=2))
sys.exit(0 if passed else 2)
