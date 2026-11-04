#!/usr/bin/env python3
"""Offline portable coordinator matrix. No compositor, systemd, or physical execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
p=HERE/'display-portable-recovery-coordinator-v0600.py'
s=importlib.util.spec_from_file_location('portable_coordinator',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
checks=[]
def check(label, condition):
    checks.append({'scenario':label,'passed':bool(condition)})
    if not condition:raise AssertionError(label)
plan={'decision':'READ_ONLY_PLAN_READY','target':'eDP-1','protected':'HDMI-A-1','original':'1920x1080@60.06Hz','candidate':'1920x1080@144.06Hz','configurationAdapter':'hyprland','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
temporal={'clock_schema':3,'recoveryRequired':True,'decision':'WAITING','transactionState':'APPLIED','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
def outcome(a=plan,b=temporal,backend='none'):
    return m.decide(a,b,backend)
for backend in ('none','systemd','runit'):
    for state in ('PREPARED','ARMED','APPLIED'):
        t=dict(temporal,transactionState=state)
        check('waiting-'+backend+'-'+state,outcome(plan,t,backend)['decision']=='OFFLINE_WAITING')
    for state in ('PREPARED','ARMED','APPLIED','ROLLBACK_REQUIRED'):
        t=dict(temporal,transactionState=state,decision='RECOVERY_DUE')
        check('due-'+backend+'-'+state,outcome(plan,t,backend)['decision']=='OFFLINE_RECOVERY_DUE')
    for state in ('CONFIRMED','ROLLED_BACK'):
        t=dict(temporal,transactionState=state,decision='TERMINAL_STATE_OBSERVED')
        check('terminal-'+backend+'-'+state,outcome(plan,t,backend)['decision']=='OFFLINE_TERMINAL')
for field,bad in [('decision','BLOCK'),('target','HDMI-A-1'),('protected','eDP-1'),('original','invalid'),('candidate','invalid'),('configurationAdapter','sway')]:
    x=dict(plan,**{field:bad})
    check('plan-reject-'+field,outcome(x)['decision']=='BLOCK_PLAN')
for decision in ('RECONCILIATION_REQUIRED_NEW_BOOT','RECONCILIATION_REQUIRED_CLOCK_DIVERGENCE','RECONCILIATION_REQUIRED_UNCERTAIN','BLOCK_IDENTITY_MISMATCH','BLOCK_NO_SCHEMA3'):
    t=dict(temporal,decision=decision)
    check('temporal-block-'+decision,outcome(plan,t)['decision']=='BLOCK_RECONCILIATION')
for state in ('CONFIRMED','ROLLED_BACK','UNCERTAIN','ROLLBACK_REQUIRED'):
    t=dict(temporal,transactionState=state)
    check('waiting-state-block-'+state,outcome(plan,t)['decision']=='BLOCK_RECONCILIATION')
for field,bad in [('clock_schema',1),('recoveryRequired',False)]:
    t=dict(temporal,**{field:bad})
    check('contract-block-'+field,outcome(plan,t)['decision']=='BLOCK_TEMPORAL_CONTRACT')
for field,bad in [('physicalExecutionEnabled',True),('executionAuthorized',True),('commandsExecuted',1),('commandsExecuted',False)]:
    for name,base,other in [('plan',plan,temporal),('temporal',temporal,plan)]:
        x=dict(base,**{field:bad})
        try:
            if name=='plan':outcome(x,other)
            else:outcome(other,x)
        except m.CoordinatorDenied:ok=True
        else:ok=False
        check('reject-unsafe-'+name+'-'+field+'-'+str(bad),ok)
for backend in ('none','systemd','runit'):
    r=outcome(backend=backend)
    check('no-physical-'+backend, r['physicalExecutionEnabled'] is False and r['commandsExecuted']==0 and r['executionAuthorized'] is False and r['rollbackPhysicalVerified'] is False)
for backend in ('unknown','SYSTEMD',''):
    try:outcome(backend=backend)
    except m.CoordinatorDenied:ok=True
    else:ok=False
    check('backend-rejected-'+str(backend),ok)
with tempfile.TemporaryDirectory() as td:
    root=Path(td); a=root/'plan.json';b=root/'temporal.json'
    a.write_text(json.dumps(plan));b.write_text(json.dumps(temporal))
    r=subprocess.run([sys.executable,'-B',str(p),'--plan-json',str(a),'--temporal-json',str(b),'--backend','runit'],capture_output=True,text=True,timeout=10)
    check('cli-positive',r.returncode==0 and json.loads(r.stdout)['decision']=='OFFLINE_WAITING')
    b.write_text('{')
    r=subprocess.run([sys.executable,'-B',str(p),'--plan-json',str(a),'--temporal-json',str(b)],capture_output=True,text=True,timeout=10)
    check('cli-corrupt-block',r.returncode==2 and json.loads(r.stdout)['decision']=='BLOCK_AND_INVESTIGATE')
    b.unlink();b.symlink_to(a)
    r=subprocess.run([sys.executable,'-B',str(p),'--plan-json',str(a),'--temporal-json',str(b)],capture_output=True,text=True,timeout=10)
    check('cli-symlink-block',r.returncode==2 and json.loads(r.stdout)['decision']=='BLOCK_AND_INVESTIGATE')
print(json.dumps({'version':'0.6.0','mode':'portable-recovery-offline-regression','selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
