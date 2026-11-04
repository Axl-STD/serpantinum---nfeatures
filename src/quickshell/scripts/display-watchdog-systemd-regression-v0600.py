#!/usr/bin/env python3
"""Consolidated offline regression for systemd watchdog coordinator."""
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wd_systemd',HERE/'display-watchdog-systemd-v0600.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

results=[]
def check(name, condition):
    results.append({'scenario':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)

def fixture(state='ARMED', phase='NONE', mode=None, delta=3, target='eDP-1'):
    tmp=tempfile.TemporaryDirectory(prefix='wd-systemd-',dir='/tmp')
    p=Path(tmp.name)
    record={'schema':1,'id':secrets.token_hex(32),'generation':2,'state':state,
            'target':target,'topology':'internal-only','plan_sha256':'a'*64,
            'boot_id':m.wd.BOOT.read_text().strip(),
            'deadline_boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)+int(delta*1e9),'phase':phase}
    m.ipc.write(p,'transaction.json',record)
    m.ipc.write(p,'simulated-ipc.json',{'connector':target,'mode':mode or m.ipc.BASE})
    m.ipc.write(p,m.wd.NAME,{'schema':1,'transaction_id':record['id'],'generation_at_arm':2,
        'transaction_digest':m.wd.digest(record),'deadline_boottime_ns':record['deadline_boottime_ns'],
        'boot_id':record['boot_id'],'status':'ARMED_SIMULATED'})
    return tmp,p,record

class Runner:
    def __init__(self, schedule=0, verify=0, stdout='LoadState=loaded\nActiveState=active\n'):
        self.schedule=schedule;self.verify=verify;self.stdout=stdout;self.commands=[]
    def __call__(self, command, **kwargs):
        self.commands.append(command)
        class P: pass
        p=P();p.returncode=self.schedule if len(self.commands)==1 else self.verify
        p.stdout='' if len(self.commands)==1 else self.stdout
        p.stderr='';return p

for target in ('eDP-1','eDP-2'):
    t,p,r=fixture(target=target)
    with t:
        unit='serpantinum-v0600-wdob-Abcdef12'
        runner=Runner()
        check(target+'_schedule',m.schedule(p,unit,runner=runner)['decision']=='SIMULATED_TIMER_VERIFIED')
        check(target+'_commands',len(runner.commands)==2 and runner.commands[0][0]=='systemd-run' and runner.commands[1][0]=='systemctl')
        check(target+'_no_physical',all('hyprctl' not in ' '.join(x) for x in runner.commands))
        check(target+'_waiting',m.run(p,max_wait=0)['decision']=='SIMULATED_NOT_APPLIED')
        check(target+'_armed',m.inspect(p)['state']=='ARMED')
        check(target+'_schedule_failure',m.schedule(p,unit,runner=Runner(schedule=1))['decision']=='BLOCK_SCHEDULE_FAILED')
        check(target+'_verify_failure',m.schedule(p,unit,runner=Runner(verify=1))['decision']=='BLOCK_TIMER_NOT_VERIFIED')
        check(target+'_timer_inactive',m.schedule(p,unit,runner=Runner(stdout='LoadState=loaded\nActiveState=inactive\n'))['decision']=='BLOCK_TIMER_NOT_VERIFIED')

def raises(f):
    try:f();return False
    except (ValueError,RuntimeError):return True

t,p,r=fixture()
with t:
    check('invalid_unit',raises(lambda:m.schedule(p,'invalid',runner=Runner())))
    check('invalid_delay_zero',raises(lambda:m.schedule(p,'serpantinum-v0600-wdob-Abcdef12',delay=0,runner=Runner())))
    check('invalid_delay_large',raises(lambda:m.schedule(p,'serpantinum-v0600-wdob-Abcdef12',delay=31,runner=Runner())))
    check('invalid_poll',raises(lambda:m.run(p,poll=0)))
    check('invalid_wait',raises(lambda:m.run(p,max_wait=121)))
    m.ipc.write(p,m.wd.NAME,{'broken':True})
    check('corrupt_receipt',raises(lambda:m.run(p,max_wait=0)))

for state,phase,mode,expected in (
    ('APPLIED','APPLY_VERIFIED',m.ipc.TARGET,'SIMULATED_RECOVERY_VERIFIED'),
    ('UNCERTAIN','ROLLBACK_INTENT',m.ipc.BASE,'SIMULATED_RECOVERY_VERIFIED'),
    ('UNCERTAIN','APPLY_INTENT',m.ipc.TARGET,'BLOCK_AND_INVESTIGATE'),
    ('ROLLED_BACK','COMPLETED',m.ipc.BASE,'SIMULATED_RECOVERY_VERIFIED'),
    ('CONFIRMED','APPLY_VERIFIED',m.ipc.TARGET,'CONFIRMED_NO_ACTION'),
):
    t,p,r=fixture(state,phase,mode,delta=-1)
    with t:
        decision=m.run(p,max_wait=0)['decision']
        check(state+'_'+phase,decision==expected)
        if expected=='SIMULATED_RECOVERY_VERIFIED':
            check(state+'_terminal',m.ipc.read(p,'transaction.json')['state']=='ROLLED_BACK')

# Ensure no service commands are run by the worker; only schedule can invoke systemd.
check('worker_has_no_hyprctl', 'hyprctl' not in (HERE/'display-watchdog-systemd-v0600.py').read_text().split('def main():')[0].replace("'hyprctl'",''))
print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,
                  'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
