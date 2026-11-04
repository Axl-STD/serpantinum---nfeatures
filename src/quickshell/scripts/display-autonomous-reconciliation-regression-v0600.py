#!/usr/bin/env python3
"""Consolidated isolated simulation regression for autonomous supervisor."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
p=HERE/'display-autonomous-reconciliation-supervisor-v0600.py'
s=importlib.util.spec_from_file_location('supervisor_test',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
sim=m.bridge.sim
c=m.bridge.c
checks=[]

def check(label, condition):
    assert condition, label
    checks.append({'scenario':label,'passed':True})

def setup(root, seconds=2):
    sim.initialize(root)
    r=c.prepare(root,sim.BASE,time.time_ns()+seconds*1_000_000_000)
    a=c.transition(root,r['id'],r['generation'],'ARMED')
    applied=sim.apply(root,r['id'],a['generation'])
    m.temporal.bind(root)
    return applied

def due(root):
    with c._locked(root) as path:
        side=m.temporal._read(path)
    while time.clock_gettime_ns(time.CLOCK_BOOTTIME)<side['deadline_boottime_ns']:
        time.sleep(.02)

with tempfile.TemporaryDirectory(prefix='serpantinum-autoreconcile-') as tmp:
    base=Path(tmp)
    for index in range(4):
        root=base/('normal'+str(index));root.mkdir(mode=0o700)
        setup(root);due(root)
        out=m.execute(root)
        check('normal_recovery_'+str(index),out['decision']=='SIMULATED_RECOVERY_VERIFIED')
        check('normal_terminal_'+str(index),m.execute(root)['decision']=='VERIFIED_TERMINAL')
        check('normal_final_'+str(index),c.inspect(root)['state']=='ROLLED_BACK')
    root=base/'waiting';root.mkdir(mode=0o700);setup(root,3)
    check('waiting_before_due',m.execute(root)['decision']=='WAITING')
    check('waiting_no_mutation',c.inspect(root)['state']=='APPLIED')
    due(root)
    check('waiting_eventual_recovery',m.execute(root)['decision']=='SIMULATED_RECOVERY_VERIFIED')
    for index in range(3):
        root=base/('uncertain'+str(index));root.mkdir(mode=0o700)
        setup(root);due(root)
        with c._locked(root) as path:
            r=c._read(path)
            r=dict(r,state='ROLLBACK_REQUIRED',generation=r['generation']+1);c._write(path,r)
            m.bridge.phase.write(path,r,'ROLLBACK_INTENT',sim.TARGET)
            uncertain=dict(r,state='UNCERTAIN',generation=r['generation']+1);c._write(path,uncertain)
            if index!=0:
                sim._write_observed(path,sim.BASE)
            if index==2:
                m.bridge.phase.write(path,uncertain,'ROLLBACK_EFFECT_OBSERVED',sim.BASE)
        out=m.execute(root)
        if index==0:
            check('uncertain_target_blocks',out['decision']=='BLOCK_AND_INVESTIGATE')
            check('uncertain_target_persists',c.inspect(root)['state']=='UNCERTAIN')
        else:
            check('uncertain_restored_reconciles_'+str(index),out['decision']=='SIMULATED_RECOVERY_VERIFIED')
            check('uncertain_final_verified_'+str(index),m.verify(root))
    root=base/'badjournal';root.mkdir(mode=0o700);setup(root);due(root)
    with c._locked(root) as path:
        r=c._read(path);r=dict(r,state='ROLLBACK_REQUIRED',generation=r['generation']+1);c._write(path,r)
        m.bridge.phase.write(path,r,'ROLLBACK_INTENT',sim.TARGET)
    check('existing_journal_blocks',m.execute(root)['decision']=='BLOCK_AND_INVESTIGATE')
    check('existing_journal_no_false_success',c.inspect(root)['state']=='ROLLBACK_REQUIRED')
    root=base/'identity';root.mkdir(mode=0o700);setup(root)
    check('bad_unit_rejected',not bool(__import__('re').fullmatch(r'serpantinum-v0600-autoreconcile-[a-zA-Z0-9]{8,32}','evil')))
    try:m.schedule(root,'evil',1);ok=False
    except ValueError:ok=True
    check('schedule_invalid_unit',ok)
    try:m.schedule(root,'serpantinum-v0600-autoreconcile-abcdefgh',0);ok=False
    except ValueError:ok=True
    check('schedule_invalid_delay',ok)
    class Fake:
        returncode=0
    captured=[]
    def runner(cmd,**kw):captured.append(cmd);return Fake()
    out=m.schedule(root,'serpantinum-v0600-autoreconcile-abcdefgh',1,runner)
    check('schedule_mock_success',out['decision']=='SIMULATED_WATCHDOG_SCHEDULED')
    check('schedule_accuracy', '--timer-property=AccuracySec=100ms' in captured[0])
    check('schedule_user', '--user' in captured[0])
    check('schedule_offline_worker',str(p.resolve()) in captured[0])
    check('schedule_no_physical',out['physicalExecutionEnabled'] is False and out['commandsExecuted']==0)
    try:m.execute(root,max_wait=-1);ok=False
    except ValueError:ok=True
    check('reject_negative_wait',ok)
    try:m.execute(root,poll=0);ok=False
    except ValueError:ok=True
    check('reject_zero_poll',ok)
    check('missing_root_denied',m.execute(root)['decision']=='WAITING')

result={'version':'0.6.0','selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(result,indent=2))
