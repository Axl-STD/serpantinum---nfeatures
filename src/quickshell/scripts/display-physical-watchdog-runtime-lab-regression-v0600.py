#!/usr/bin/env python3
"""Offline contract regression. No systemd units or physical commands."""
import importlib.util,json,secrets,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'display-physical-watchdog-runtime-lab-v0600.py'
s=importlib.util.spec_from_file_location('wdlab',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
w=m.shared; ipc=w.ipc
results=[]
def check(name,fn):
    try: fn();results.append(dict(scenario=name,passed=True))
    except Exception as e: results.append(dict(scenario=name,passed=False,error=str(e)))
def fixture():
    t=tempfile.TemporaryDirectory(prefix='serpantinum-pwlab-');root=Path(t.name);root.chmod(0o700)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    base=dict(name='eDP-1',description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)
    r=dict(schema=1,id=secrets.token_hex(32),generation=4,state='ROLLBACK_REQUIRED',target='eDP-1',topology='internal-only',plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1000000,phase='APPLY_VERIFIED',execution_backend='physical')
    keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    with ipc.locked(root) as path:
        ipc.write(path,'transaction.json',r)
        ipc.write(path,'physical-snapshot.json',dict(transaction_id=r['id'],boot_id=boot,target=r['target'],topology=r['topology'],snapshot=[base],snapshot_sha256=w.sha([base]),identity={'instance':'lab_instance'}))
        ipc.write(path,'physical-watchdog-rehearsal.json',dict(transaction_id=r['id'],transaction_digest=w.sha({k:r[k] for k in keys}),status='ARMED_LAB_ONLY'))
        ipc.write(path,'shared-lab-monitor.json',dict(schema=1,instance='lab_instance',monitors=[dict(base,refreshRate=144.06)]))
    return t,root

def scenario(kind):
    t,root=fixture()
    try:
        if kind=='normal': assert m.run(root)['decision']=='LAB_RECOVERY_VERIFIED_NOT_PHYSICAL'
        elif kind=='terminal':
            assert m.run(root)['decision']=='LAB_RECOVERY_VERIFIED_NOT_PHYSICAL'
            assert m.run(root)['detail']=='LAB_TERMINAL_VERIFIED'
        elif kind=='after-intent':
            w.recover_lab(root,w.SharedLabTransport(root),failpoint='after-intent')
            assert m.run(root)['decision']=='LAB_RECOVERY_VERIFIED_NOT_PHYSICAL'
        elif kind=='after-effect':
            w.recover_lab(root,w.SharedLabTransport(root),failpoint='after-effect')
            assert m.run(root)['decision']=='LAB_RECOVERY_VERIFIED_NOT_PHYSICAL'
        elif kind=='armed':
            with ipc.locked(root) as path:
                r=ipc.read(path,'transaction.json');r.update(state='ARMED',phase='NONE');ipc.write(path,'transaction.json',r)
            assert m.run(root)['decision']=='LAB_ARMED_NO_EFFECT'
        elif kind=='confirmed':
            with ipc.locked(root) as path:
                r=ipc.read(path,'transaction.json');r.update(state='CONFIRMED');ipc.write(path,'transaction.json',r)
            assert m.run(root)['decision']=='CONFIRMED_NO_ACTION'
        elif kind=='waiting':
            with ipc.locked(root) as path:
                r=ipc.read(path,'transaction.json');r.update(state='APPLIED',deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+10000000000);ipc.write(path,'transaction.json',r)
                receipt=ipc.read(path,'physical-watchdog-rehearsal.json');keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns');receipt['transaction_digest']=w.sha({k:r[k] for k in keys});ipc.write(path,'physical-watchdog-rehearsal.json',receipt)
            assert m.run(root,max_wait=0)['decision']=='WAITING'
        elif kind in ('backend','receipt','snapshot','identity','hotplug','corrupt','boot'):
            with ipc.locked(root) as path:
                if kind=='backend':
                    r=ipc.read(path,'transaction.json');r['execution_backend']='simulated';ipc.write(path,'transaction.json',r)
                if kind=='receipt':
                    r=ipc.read(path,'physical-watchdog-rehearsal.json');r['status']='ARMED_PHYSICAL';ipc.write(path,'physical-watchdog-rehearsal.json',r)
                if kind=='snapshot':
                    r=ipc.read(path,'physical-snapshot.json');r['snapshot_sha256']='bad';ipc.write(path,'physical-snapshot.json',r)
                if kind=='identity':
                    r=ipc.read(path,'shared-lab-monitor.json');r['instance']='different';ipc.write(path,'shared-lab-monitor.json',r)
                if kind=='hotplug':
                    r=ipc.read(path,'shared-lab-monitor.json');r['monitors'].append(dict(r['monitors'][0],name='DP-1'));ipc.write(path,'shared-lab-monitor.json',r)
                if kind=='boot':
                    r=ipc.read(path,'transaction.json');r['boot_id']='old';ipc.write(path,'transaction.json',r)
            if kind=='corrupt': (root/'shared-lab-monitor.json').write_text('{bad')
            try: m.validate(root);raise AssertionError('accepted unsafe')
            except (w.Denied,ipc.Denied,OSError,ValueError,KeyError,TypeError): pass
        elif kind in ('negative_wait','long_wait','zero_poll','huge_poll'):
            kwargs={'negative_wait':dict(max_wait=-1),'long_wait':dict(max_wait=121),'zero_poll':dict(poll=0),'huge_poll':dict(poll=3)}[kind]
            try: m.run(root,**kwargs);raise AssertionError('accepted invalid')
            except ValueError: pass
        elif kind in ('bad_unit','bad_delay','expired_schedule'):
            if kind=='expired_schedule':
                with ipc.locked(root) as path:
                    r=ipc.read(path,'transaction.json');r.update(state='ARMED',phase='NONE');ipc.write(path,'transaction.json',r)
            try: m.schedule(root,'unsafe' if kind=='bad_unit' else m.PREFIX+'Abcdefgh',delay=0 if kind=='bad_delay' else 1,runner=lambda *a,**k: (_ for _ in ()).throw(AssertionError('external command')));raise AssertionError('accepted invalid')
            except ValueError: pass
        elif kind=='no_commands':
            assert m.run(root)['commandsExecuted']==0
        else: raise AssertionError(kind)
    finally:t.cleanup()
for kind in ('normal','terminal','after-intent','after-effect','armed','confirmed','waiting','backend','receipt','snapshot','identity','hotplug','corrupt','boot','negative_wait','long_wait','zero_poll','huge_poll','bad_unit','bad_delay','expired_schedule','no_commands'):
    check(kind,lambda k=kind:scenario(k))
passed=all(x['passed'] for x in results)
print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,executionAuthorized=False,commandsExecuted=0,rollbackPhysicalVerified=False),indent=2))
raise SystemExit(0 if passed else 1)
