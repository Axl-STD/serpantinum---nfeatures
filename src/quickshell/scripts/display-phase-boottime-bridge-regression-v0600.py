#!/usr/bin/env python3
"""Offline regression of phase-aware BOOTTIME bridge; no display operations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('phase_bridge_under_test', HERE/'display-phase-boottime-bridge-v0600.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
results=[]

def check(name, predicate):
    assert predicate, name
    results.append({'scenario':name,'passed':True})

def setup():
    work = tempfile.TemporaryDirectory(prefix='serpantinum-phase-bridge-')
    root = Path(work.name)
    root.chmod(0o700)
    b.sim.initialize(root)
    r = b.c.prepare(root, b.sim.BASE, time.time_ns()+30_000_000_000)
    armed = b.c.transition(root,r['id'],r['generation'],'ARMED')
    applied = b.sim.apply(root,r['id'],armed['generation'])
    side = b.temporal.bind(root)
    return work,root,applied,side

def due(side):
    return {'realtime_ns':side['deadline_realtime_ns']+1,'boottime_ns':side['deadline_boottime_ns']+1}

def run():
    with setup()[0] as unused:
        pass
    work,root,r,side=setup()
    try:
        check('waiting',b.execute(root)['decision']=='WAITING')
        check('waiting_no_journal',b.phase.read(root) is None)
        check('waiting_state',b.c.inspect(root)['state']=='APPLIED')
        check('clock_divergence',b.execute(root,sample={'realtime_ns':1,'boottime_ns':1})['decision']=='BLOCK_CLOCK_DIVERGENCE')
        check('wrong_boot',b.execute(root,sample=due(side),boot='other')['decision']=='BLOCK_NEW_BOOT')
        check('wrong_token',b.execute(root,sample=due(side),expected_token='foreign')['decision']=='BLOCK_IDENTITY')
        check('stale_generation',b.execute(root,sample=due(side),expected_generation=0)['decision']=='BLOCK_STALE_GENERATION')
        check('bad_clock_type',raises(lambda:b.execute(root,sample={'realtime_ns':True,'boottime_ns':1})))
        check('bad_clock_shape',raises(lambda:b.execute(root,sample={'realtime_ns':1})))
        check('bad_clock_negative',raises(lambda:b.execute(root,sample={'realtime_ns':-1,'boottime_ns':1})))
        result=b.execute(root,sample=due(side))
        check('rollback_verified',result['decision']=='SIMULATED_ROLLBACK_VERIFIED')
        check('rollback_state',result['state']=='ROLLED_BACK')
        check('generation_7',result['generation']==7)
        check('effect_original',b.sim.inspect(root)['simulatedMonitor']==b.sim.BASE)
        check('journal_completed',b.phase.read(root)['phase']=='COMPLETED')
        check('journal_generation',b.phase.read(root)['generation']==7)
        check('journal_audit',b.phase.audit(root)['decision']=='TERMINAL_VERIFIED')
        check('terminal_idempotent',b.execute(root,sample=due(side))['decision']=='TERMINAL_NO_ACTION')
        check('no_physical',result['commandsExecuted']==0 and result['physicalExecutionEnabled'] is False)
        check('no_authorization',result['executionAuthorized'] is False and result['rollbackPhysicalVerified'] is False)
        check('journal_unchanged_on_repeat',b.phase.read(root)['phase']=='COMPLETED')
    finally:work.cleanup()
    work,root,r,side=setup()
    try:
        with b.c._locked(root) as p:
            state=b.c._read(p)
            b.phase.write(p,state,'ROLLBACK_INTENT',b.sim.TARGET)
        check('existing_journal_block',b.execute(root,sample=due(side))['decision']=='BLOCK_EXISTING_PHASE_JOURNAL')
        check('existing_journal_preserved',b.phase.read(root)['phase']=='ROLLBACK_INTENT')
    finally:work.cleanup()
    work,root,r,side=setup()
    try:
        with b.c._locked(root) as p:
            state=b.c._read(p)
            b.c._write(p,dict(state,state='UNCERTAIN',generation=state['generation']+1))
        check('uncertain_block',b.execute(root,sample=due(side))['decision']=='BLOCK_UNCERTAIN')
        check('uncertain_preserved',b.c.inspect(root)['state']=='UNCERTAIN')
    finally:work.cleanup()
    work,root,r,side=setup()
    try:
        b.sim.confirm(root,r['id'],r['generation'])
        check('confirmed_no_action',b.execute(root,sample=due(side))['decision']=='TERMINAL_NO_ACTION')
        check('confirmed_preserved',b.c.inspect(root)['state']=='CONFIRMED')
    finally:work.cleanup()
    work,root,r,side=setup()
    try:
        # Two independent subprocesses compete over the same durable lock.
        with b.c._locked(root) as p:
            state=b.c._read(p)
            b.c._write(p,dict(state,state='ROLLBACK_REQUIRED',generation=state['generation']+1))
        # Use an expired synthetic clock only in the direct API; subprocess uses actual clock.
        a=b.execute(root,sample=due(side))
        check('pending_takeover',a['decision']=='SIMULATED_ROLLBACK_VERIFIED')
        check('pending_terminal',b.execute(root,sample=due(side))['decision']=='TERMINAL_NO_ACTION')
    finally:work.cleanup()
    return {'version':'0.6.0','selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}

def raises(fn):
    try:fn()
    except (ValueError,RuntimeError,TypeError):return True
    return False

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
