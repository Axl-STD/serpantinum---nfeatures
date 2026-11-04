#!/usr/bin/env python3
"""Offline-only multi-process recovery races. No physical display calls."""
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent

def load(name,key):
    p=HERE/name
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency: '+name)
    spec=importlib.util.spec_from_file_location(key,p)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sim=load('display-recovery-simulator-v0600.py','race_sim')
c=sim.controller

def child(root,token,gen,mode,barrier,queue):
    try:
        barrier.wait(timeout=8)
        if mode=='confirm':
            result=sim.confirm(root,token,gen)
        elif mode=='expire':
            result={'state':c.expire(root)}
        elif mode=='rollback':
            result=sim.rollback(root,token,gen)
        else:
            raise ValueError('unknown mode')
        queue.put((mode,'ok',result['state']))
    except Exception as exc:
        queue.put((mode,'denied',type(exc).__name__))

def run_pair(root,token,gen,modes):
    ctx=mp.get_context('fork')
    barrier=ctx.Barrier(len(modes))
    queue=ctx.Queue()
    procs=[ctx.Process(target=child,args=(str(root),token,gen,m,barrier,queue)) for m in modes]
    for p in procs:p.start()
    for p in procs:
        p.join(15)
        if p.is_alive():
            p.kill();p.join()
            raise RuntimeError('worker timeout')
        if p.exitcode!=0:raise RuntimeError('worker crashed')
    return [queue.get(timeout=2) for _ in modes]

def main():
    results=[]
    def check(name,ok):
        results.append({'scenario':name,'passed':bool(ok)})
        if not ok:raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-race-') as td:
        for i in range(3):
            root=Path(td)/('race-'+str(i));root.mkdir(mode=0o700)
            sim.initialize(root)
            rec=c.prepare(root,sim.BASE,time.time_ns()+60_000_000_000)
            armed=c.transition(root,rec['id'],1,'ARMED')
            applied=sim.apply(root,rec['id'],2)
            check(f'{i}-applied',applied['state']=='APPLIED' and applied['generation']==4)
            # Competing confirmations: exactly one can consume generation 4.
            outcome=run_pair(root,rec['id'],4,['confirm','confirm'])
            check(f'{i}-one-confirm',sum(x[1]=='ok' for x in outcome)==1)
            check(f'{i}-one-denied',sum(x[1]=='denied' for x in outcome)==1)
            final=sim.inspect(root)
            check(f'{i}-terminal',final['recovery']['state']=='CONFIRMED' and final['recovery']['generation']==5)
            check(f'{i}-target-stable',final['simulatedMonitor']==sim.TARGET)
        for i in range(3):
            root=Path(td)/('expire-'+str(i));root.mkdir(mode=0o700)
            sim.initialize(root)
            rec=c.prepare(root,sim.BASE,time.time_ns()+2_000_000_000)
            c.transition(root,rec['id'],1,'ARMED')
            sim.apply(root,rec['id'],2)
            # Force expiry before contention, no wall-clock races in expected outcome.
            time.sleep(2.1)
            outcome=run_pair(root,rec['id'],4,['confirm','expire'])
            check(f'{i}-expire-wins',any(x[0]=='expire' and x[1]=='ok' and x[2]=='ROLLBACK_REQUIRED' for x in outcome))
            check(f'{i}-confirm-denied',any(x[0]=='confirm' and x[1]=='denied' for x in outcome))
            pending=c.inspect(root)
            check(f'{i}-pending',pending['state']=='ROLLBACK_REQUIRED' and pending['generation']==5)
            check(f'{i}-effect-still-target',sim.inspect(root)['simulatedMonitor']==sim.TARGET)
            outcome=run_pair(root,rec['id'],5,['rollback','rollback'])
            check(f'{i}-one-rollback',sum(x[1]=='ok' for x in outcome)==1)
            check(f'{i}-one-rollback-denied',sum(x[1]=='denied' for x in outcome)==1)
            final=sim.inspect(root)
            check(f'{i}-rolled-back',final['recovery']['state']=='ROLLED_BACK' and final['recovery']['generation']==7)
            check(f'{i}-restored',final['simulatedMonitor']==sim.BASE)
    return {'version':'0.6.0','mode':'offline-recovery-multiprocess-races','selftest':'passed','totalScenarios':len(results),'results':results,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'limitations':['No physical rollback, systemd or TTY integration','No crash/power-loss durability guarantee','Same-UID hostile pathname races remain outside scope']}

if __name__=='__main__':
    try:print(json.dumps(main(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc)}))
        sys.exit(2)
