#!/usr/bin/env python3
"""Offline tests for the real systemd command contract, without launching units."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('autonomous', HERE/'display-physical-autonomous-supervisor-v0600.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
checks=[]
def check(name, cond):
    checks.append({'scenario':name,'passed':bool(cond)})
    if not cond: raise AssertionError(name)
def denied(name, func):
    try: func()
    except (m.SupervisorDenied, m.integration.c.RecoveryDenied): check(name, True)
    else: check(name, False)
class Runner:
    def __init__(self, rc=0): self.calls=[]; self.rc=rc
    def __call__(self, command, **kwargs):
        self.calls.append((command,kwargs))
        return subprocess.CompletedProcess(command,self.rc,'','')
try:
    with tempfile.TemporaryDirectory(prefix='serpantinum-autosim-') as tmp:
        root=Path(tmp)/'transaction';root.mkdir(mode=0o700)
        unit='serpantinum-v0600-autosim-12345678'
        denied('missing-transaction',lambda:m.plan(root,unit))
        m.integration.prepare(root,seconds=60)
        command=m.plan(root,unit)
        check('systemd-run',command[0]=='systemd-run')
        check('user-service','--user' in command)
        check('oneshot','--property=Type=oneshot' in command)
        check('delay','--on-active=8s' in command)
        check('unit','--unit='+unit in command)
        check('worker',str(m.worker) in command)
        check('root',str(root) in command)
        check('python-no-bytecode','-B' in command)
        check('no-shell',all(x not in command for x in ('sh','bash','-c')))
        denied('bad-unit',lambda:m.plan(root,'bad'))
        denied('unit-injection',lambda:m.plan(root,unit+';touch'))
        denied('unit-slash',lambda:m.plan(root,unit+'/x'))
        denied('short-delay',lambda:m.plan(root,unit,4))
        denied('long-delay',lambda:m.plan(root,unit,121))
        denied('boolean-delay',lambda:m.plan(root,unit,True))
        runner=Runner()
        outcome=m.schedule(root,unit,8,runner)
        check('scheduled',outcome['decision']=='SIMULATED_WATCHDOG_SCHEDULED')
        check('runner-once',len(runner.calls)==1)
        check('runner-timeout',runner.calls[0][1]['timeout']==15)
        check('offline',outcome['physicalExecutionEnabled'] is False and outcome['commandsExecuted']==0)
        bad=Runner(1)
        denied('schedule-failure',lambda:m.schedule(root,unit,8,bad))
        check('failure-called-once',len(bad.calls)==1)
        record=m.integration.c.inspect(root)
        check('state-still-applied',record['state']=='APPLIED')
        check('monitor-still-target',m.integration.inspect(root)['simulatedMonitor']==m.integration.sim.TARGET)
        denied('deadline-margin',lambda:m.plan(root,unit,59))
        confirmed=m.integration.sim.confirm(root,record['id'],record['generation'])
        check('confirmed',confirmed['state']=='CONFIRMED')
        denied('confirmed-no-schedule',lambda:m.plan(root,unit))
        check('no-physical-source',not any(s in Path(m.__file__).read_text() for s in ('hyprctl keyword','hyprctl dispatch','monitorv2=')))
    print(json.dumps({'version':'0.6.0','mode':'offline-autonomous-supervisor','selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),'totalScenarios':len(checks),'results':checks},indent=2))
    sys.exit(2)
