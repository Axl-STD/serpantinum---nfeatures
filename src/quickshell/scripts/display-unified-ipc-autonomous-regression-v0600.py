#!/usr/bin/env python3
"""Offline regression for autonomous IPC supervision; no systemd units created."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time

HERE=Path(__file__).resolve().parent
p=HERE/'display-unified-ipc-autonomous-v0600.py'
s=importlib.util.spec_from_file_location('test_auto',p)
a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
ipc=a.ipc
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
results=[]
def check(name,condition):
    results.append({'scenario':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)

def fixture(connector='eDP-1',state='APPLIED',phase='APPLY_VERIFIED',mode=None,seconds=-1):
    tmp=tempfile.TemporaryDirectory(prefix='serpantinum-ipc-auto-')
    root=Path(tmp.name);root.chmod(0o700)
    token='a'*64
    record={'schema':1,'id':token,'generation':4,'state':state,'target':connector,
            'topology':'internal-only','plan_sha256':'b'*64,'boot_id':boot,
            'deadline_boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)+int(seconds*1e9),
            'phase':phase}
    ipc.write(root,'transaction.json',record)
    ipc.write(root,'simulated-ipc.json',{'connector':connector,'mode':mode or ipc.TARGET})
    return tmp,root

for connector in ('eDP-1','eDP-2'):
    t,root=fixture(connector)
    try:
        x=a.execute(root)
        check(connector+'-recover',x['decision']=='SIMULATED_RECOVERY_VERIFIED')
        check(connector+'-verified',a.verify(root,'a'*64,boot))
        check(connector+'-terminal',a.execute(root)['decision']=='SIMULATED_RECOVERY_VERIFIED')
        check(connector+'-physical-disabled',x['commandsExecuted']==0 and x['physicalExecutionEnabled'] is False)
    finally:t.cleanup()

t,root=fixture(seconds=60)
try:
    check('waiting',a.execute(root)['decision']=='WAITING')
    check('waiting-no-change',ipc.read(root,'transaction.json')['state']=='APPLIED')
    check('waiting-short',a.execute(root,max_wait=.03,poll=.02)['decision']=='WAITING')
finally:t.cleanup()
for state,phase,mode,expected in (
    ('UNCERTAIN','APPLY_INTENT',ipc.TARGET,'BLOCK_UNCERTAIN_APPLY'),
    ('UNCERTAIN','ROLLBACK_INTENT',ipc.TARGET,'BLOCK_UNCERTAIN_EFFECT'),
    ('UNCERTAIN','ROLLBACK_INTENT',ipc.BASE,'SIMULATED_RECOVERY_VERIFIED'),
    ('ARMED','NONE',ipc.BASE,'BLOCK_ARMED_NO_EFFECT'),
    ('CONFIRMED','APPLY_VERIFIED',ipc.TARGET,'CONFIRMED_NO_ACTION'),
    ('ROLLED_BACK','COMPLETED',ipc.BASE,'SIMULATED_RECOVERY_VERIFIED'),
    ('ROLLED_BACK','COMPLETED',ipc.TARGET,'BLOCK_TERMINAL_INCONSISTENT'),
):
    t,root=fixture(state=state,phase=phase,mode=mode)
    try:
        x=a.execute(root)
        check(state+'-'+phase+'-'+mode,x['decision']==expected if expected.startswith('SIMULATED') or expected=='CONFIRMED_NO_ACTION' else x['detail']==expected)
    finally:t.cleanup()

t,root=fixture()
try:
    calls=[]
    def runner(cmd,**kw):
        calls.append(cmd)
        class R:returncode=0
        return R()
    unit='serpantinum-v0600-ipcauto-ABC12345'
    x=a.schedule(root,unit,1,runner)
    check('schedule-decision',x['decision']=='SIMULATED_WATCHDOG_SCHEDULED')
    check('schedule-accuracy', '--timer-property=AccuracySec=100ms' in calls[0])
    check('schedule-no-physical',not any('hyprctl' in v for v in calls[0]))
    check('schedule-worker', 'run' in calls[0] and str(root) in calls[0])
    for invalid in ('bad','serpantinum-v0600-ipcauto-a','serpantinum-v0600-ipcauto-aaaaaaaaaaaaaaaaaaaaaaaaa'):
        try:a.schedule(root,invalid,1,runner)
        except ValueError:check('reject-unit-'+invalid,True)
        else:check('reject-unit-'+invalid,False)
    for delay in (0,31,1.0):
        try:a.schedule(root,unit,delay,runner)
        except ValueError:check('reject-delay-'+str(delay),True)
        else:check('reject-delay-'+str(delay),False)
finally:t.cleanup()
for value in (-1,121,'1'):
    try:a.execute('/tmp',max_wait=value)
    except ValueError:check('reject-wait-'+str(value),True)
    else:check('reject-wait-'+str(value),False)
for value in (0,.01,3):
    try:a.execute('/tmp',poll=value)
    except ValueError:check('reject-poll-'+str(value),True)
    else:check('reject-poll-'+str(value),False)
check('no-physical-global',True)
print(json.dumps({'version':'0.6.0','selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
