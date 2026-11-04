#!/usr/bin/env python3
"""Multiprocess SIGKILL fault matrix for the *real* offline phase BOOTTIME bridge.
Only child Python processes created by this test are killed. No Hyprland APIs.
"""
import importlib.util, json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('phase_crash_bridge',HERE/'display-phase-boottime-bridge-v0600.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
POINTS=('after-due','after-intent','after-uncertain','after-effect','after-effect-journal','after-terminal','after-completed')
results=[]
def check(label,ok):
    if not ok:raise AssertionError(label)
    results.append({'scenario':label,'passed':True})
def setup(root):
    root.mkdir(mode=0o700)
    b.sim.initialize(root)
    r=b.c.prepare(root,b.sim.BASE,time.time_ns()+2_000_000_000)
    a=b.c.transition(root,r['id'],r['generation'],'ARMED')
    applied=b.sim.apply(root,r['id'],a['generation'])
    b.temporal.bind(root)
    return applied

def child(root,point):
    original_write=b.c._write
    original_phase=b.phase.write
    original_effect=b.sim._write_observed
    def kill():os.kill(os.getpid(),signal.SIGKILL)
    def state_write(path,record):
        result=original_write(path,record)
        if (point=='after-due' and record['state']=='ROLLBACK_REQUIRED') or (point=='after-uncertain' and record['state']=='UNCERTAIN') or (point=='after-terminal' and record['state']=='ROLLED_BACK'):kill()
        return result
    def journal_write(path,record,phase,observed):
        result=original_phase(path,record,phase,observed)
        if (point=='after-intent' and phase=='ROLLBACK_INTENT') or (point=='after-effect-journal' and phase=='ROLLBACK_EFFECT_OBSERVED') or (point=='after-completed' and phase=='COMPLETED'):kill()
        return result
    def effect_write(path,value):
        result=original_effect(path,value)
        if point=='after-effect' and value==b.sim.BASE:kill()
        return result
    b.c._write=state_write;b.phase.write=journal_write;b.sim._write_observed=effect_write
    side=b.temporal._read(root)
    sample={'realtime_ns':side['deadline_realtime_ns']+1,'boottime_ns':side['deadline_boottime_ns']+1}
    b.execute(root,sample=sample)
    raise AssertionError('fault did not trigger')

def run():
    for point in POINTS:
        with tempfile.TemporaryDirectory(prefix='serpantinum-phase-crash-') as tmp:
            root=Path(tmp)/'transaction';setup(root)
            proc=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--child',str(root),point],capture_output=True,text=True,timeout=15)
            check(point+':sigkill',proc.returncode==-signal.SIGKILL)
            state=b.c.inspect(root);journal=b.phase.read(root);observed=b.sim.inspect(root)['simulatedMonitor']
            check(point+':identity',state['id']==b.temporal._read(root)['transaction_id'])
            check(point+':generation',state['generation'] in (5,6,7))
            check(point+':no-physical',observed in (b.sim.BASE,b.sim.TARGET))
            side=b.temporal._read(root);sample={'realtime_ns':side['deadline_realtime_ns']+1,'boottime_ns':side['deadline_boottime_ns']+1}
            if point=='after-due':
                check(point+':pending',state['state']=='ROLLBACK_REQUIRED' and journal is None)
                recovery=b.execute(root,sample=sample)
                check(point+':takeover',recovery['decision']=='SIMULATED_ROLLBACK_VERIFIED')
                check(point+':terminal',b.phase.audit(root)['decision']=='TERMINAL_VERIFIED')
            elif point in ('after-terminal','after-completed'):
                check(point+':terminal',state['state']=='ROLLED_BACK' and observed==b.sim.BASE)
                check(point+':journal',journal is not None)
                check(point+':no-repeat',b.execute(root,sample=sample)['decision']=='TERMINAL_NO_ACTION')
                if point=='after-completed':check(point+':verified',b.phase.audit(root)['decision']=='TERMINAL_VERIFIED')
                else:check(point+':incomplete-journal',b.phase.audit(root)['decision']=='BLOCK_INCONSISTENT')
            else:
                check(point+':uncertain-or-intent',state['state'] in ('ROLLBACK_REQUIRED','UNCERTAIN'))
                check(point+':journal',journal is not None)
                decision=b.execute(root,sample=sample)['decision']
                check(point+':fail-closed',decision in ('BLOCK_UNCERTAIN','BLOCK_EXISTING_PHASE_JOURNAL'))
                check(point+':no-false-terminal',b.c.inspect(root)['state']!='ROLLED_BACK')
    return {'version':'0.6.0','selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}
if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--child':child(Path(sys.argv[2]),sys.argv[3])
    else:print(json.dumps(run(),indent=2))
