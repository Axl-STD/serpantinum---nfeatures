#!/usr/bin/env python3
"""Offline bridge regression with deterministic clocks and real process contention."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent

def load(name,alias):
    p=HERE/name;s=importlib.util.spec_from_file_location(alias,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
bridge=load('display-portable-runtime-bridge-v0600.py','bridge_test')
sim=bridge.sim;c=sim.controller;t=bridge.temporal
checks=[]
def check(name,ok):
    checks.append({'scenario':name,'passed':bool(ok)})
    if not ok:raise AssertionError(name)

def setup(td, phase='APPLIED'):
    root=Path(td)/('txn'+str(len(checks)));root.mkdir(mode=0o700)
    sim.initialize(root)
    r=c.prepare(root,sim.BASE,time.time_ns()+30_000_000_000)
    a=c.transition(root,r['id'],r['generation'],'ARMED')
    if phase=='APPLIED':r=sim.apply(root,r['id'],a['generation'])
    else:r=a
    now={'realtime_ns':time.time_ns(),'boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)}
    side=t.bind(root,now=now,current_boot='11111111-1111-1111-1111-111111111111')
    return root,r,side,now

def sample(side,seconds=0,offset=0):
    return {'boottime_ns':side['deadline_boottime_ns']+seconds*1_000_000_000,
            'realtime_ns':side['deadline_realtime_ns']+seconds*1_000_000_000+offset}

try:
 with tempfile.TemporaryDirectory(prefix='serpantinum-bridge-test-') as td:
    root,r,side,now=setup(td)
    boot=side['boot_id']
    check('waiting',bridge.execute(root,sample=now,current_boot=boot)['decision']=='WAITING')
    check('no-mutation-while-waiting',c.inspect(root)['generation']==r['generation'])
    check('wrong-boot',bridge.execute(root,sample=now,current_boot='22222222-2222-2222-2222-222222222222')['decision']=='BLOCK_NEW_BOOT')
    check('wrong-token',bridge.execute(root,sample=now,current_boot=boot,expected_token='x')['decision']=='BLOCK_IDENTITY_MISMATCH')
    check('stale-generation',bridge.execute(root,sample=now,current_boot=boot,expected_generation=r['generation']+1)['decision']=='BLOCK_STALE_GENERATION')
    drift={'boottime_ns':now['boottime_ns'],'realtime_ns':now['realtime_ns']+5_000_000_000}
    check('clock-drift',bridge.execute(root,sample=drift,current_boot=boot)['decision']=='BLOCK_CLOCK_DIVERGENCE')
    check('no-mutation-on-drift',c.inspect(root)['state']=='APPLIED')
    due=sample(side)
    check('due-rollback',bridge.execute(root,sample=due,current_boot=boot)['decision']=='SIMULATED_ROLLBACK_VERIFIED')
    check('terminal-state',c.inspect(root)['state']=='ROLLED_BACK')
    check('base-restored',sim.inspect(root)['simulatedMonitor']==sim.BASE)
    check('idempotent',bridge.execute(root,sample=due,current_boot=boot)['decision']=='TERMINAL_NO_ACTION')
    root2,r2,s2,n2=setup(td,'ARMED')
    check('armed-due',bridge.execute(root2,sample=sample(s2),current_boot=s2['boot_id'])['decision']=='SIMULATED_ROLLBACK_VERIFIED')
    check('armed-base',sim.inspect(root2)['simulatedMonitor']==sim.BASE)
    root3,r3,s3,n3=setup(td)
    with c._locked(root3) as locked:
        c._write(locked,dict(c._read(locked),state='UNCERTAIN',generation=r3['generation']+1))
    check('uncertain-block',bridge.execute(root3,sample=sample(s3),current_boot=s3['boot_id'])['decision']=='BLOCK_UNCERTAIN')
    check('uncertain-preserved',c.inspect(root3)['state']=='UNCERTAIN')
    root4,r4,s4,n4=setup(td)
    (root4/'temporal-schema3.json').unlink()
    check('missing-sidecar',bridge.execute(root4,sample=n4,current_boot=s4['boot_id'])['decision']=='BLOCK_INCOMPLETE_TRANSACTION')
    root5,r5,s5,n5=setup(td)
    with c._locked(root5) as locked:
        c._write(locked,dict(c._read(locked),deadline_ns=c._read(locked)['deadline_ns']+1,generation=r5['generation']+1))
    check('identity-mismatch',bridge.execute(root5,sample=n5,current_boot=s5['boot_id'])['decision']=='BLOCK_IDENTITY_MISMATCH')
    root6,r6,s6,n6=setup(td)
    check('boottime-priority',bridge.execute(root6,sample=sample(s6,0,-8_000_000_000),current_boot=s6['boot_id'])['decision']=='SIMULATED_ROLLBACK_VERIFIED')
    root7,r7,s7,n7=setup(td)
    check('predeadline-near',bridge.execute(root7,sample={'boottime_ns':s7['deadline_boottime_ns']-1,'realtime_ns':s7['deadline_realtime_ns']-1},current_boot=s7['boot_id'])['decision']=='WAITING')
    check('no-physical',bridge.execute(root7,sample=n7,current_boot=s7['boot_id'])['commandsExecuted']==0)
    for invalid in ({}, {'boottime_ns':1}, {'realtime_ns':1,'boottime_ns':True},{'realtime_ns':0,'boottime_ns':1}):
        try:bridge.execute(root7,sample=invalid,current_boot=s7['boot_id'])
        except bridge.BridgeDenied:ok=True
        else:ok=False
        check('invalid-sample-'+str(len(checks)),ok)
    check('old-worker-unchanged', (HERE/'display-deadline-aligned-worker-v0600.py').is_file())
    check('coordinator-unchanged',(HERE/'display-portable-recovery-coordinator-v0600.py').is_file())
    check('schema3-unchanged',(HERE/'display-temporal-schema3-v0600.py').is_file())
    check('no-physical-interface', 'hyprctl' not in (HERE/'display-portable-runtime-bridge-v0600.py').read_text())
 print(json.dumps({'version':'0.6.0','mode':'portable-runtime-bridge-regression','selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
 print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),'totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2));sys.exit(2)
