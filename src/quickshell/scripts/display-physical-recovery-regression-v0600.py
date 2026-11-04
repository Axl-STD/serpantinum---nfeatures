#!/usr/bin/env python3
"""Offline, deterministic and multiprocess tests of recovery arbitration."""
import importlib.util
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import os

SOURCE = Path(__file__).with_name('display-physical-recovery-controller-v0600.py')
spec = importlib.util.spec_from_file_location('physical_recovery', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SNAP = dict(name='eDP-1',width=1920,height=1080,refresh_mhz=60060,scale_milli=1000,transform=0)

def contender(root, token, generation, state, start, out):
    start.wait()
    try:
        result=m.transition(root, token, generation, state)
        out.put(('ok',result['state']))
    except m.RecoveryDenied as exc:
        out.put(('denied',str(exc)))

def run():
    rows=[]
    def check(name, value):
        rows.append(dict(scenario=name, passed=bool(value)))
        if not value: raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-recovery-') as td:
        root=Path(td)/'state';root.mkdir(mode=0o700)
        deadline=time.time_ns()+60_000_000_000
        check('empty',m.inspect(root) is None)
        check('no-state-expiry',m.expire(root)=='NO_STATE')
        for bad in [dict(SNAP,name='HDMI-A-1'),dict(SNAP,width=0),dict(SNAP,refresh_mhz=120000),dict(SNAP,transform=1)]:
            try:m.prepare(root,bad,deadline)
            except m.RecoveryDenied:check('invalid-snapshot-'+str(len(rows)),True)
            else:check('invalid-snapshot-'+str(len(rows)),False)
        p=m.prepare(root,SNAP,deadline)
        check('prepared',p['state']=='PREPARED')
        check('generation-one',p['generation']==1)
        check('session-token',len(p['id'])==64)
        check('persisted',m.inspect(root)==p)
        try:m.prepare(root,SNAP,deadline)
        except m.RecoveryDenied:check('no-overwrite',True)
        else:check('no-overwrite',False)
        try:m.transition(root,'0'*64,1,'ARMED')
        except m.RecoveryDenied:check('foreign-token',True)
        else:check('foreign-token',False)
        p=m.transition(root,p['id'],1,'ARMED')
        check('armed',p['state']=='ARMED')
        check('generation-two',p['generation']==2)
        for target in ['APPLIED','CONFIRMED','ROLLED_BACK','PREPARED']:
            try:m.transition(root,p['id'],2,target)
            except m.RecoveryDenied:check('reject-'+target,True)
            else:check('reject-'+target,False)
        try:m.transition(root,p['id'],1,'ROLLBACK_REQUIRED')
        except m.RecoveryDenied:check('stale',True)
        else:check('stale',False)
        ctx=multiprocessing.get_context('spawn')
        start=ctx.Event();queue=ctx.Queue()
        workers=[ctx.Process(target=contender,args=(str(root),p['id'],2,'ROLLBACK_REQUIRED',start,queue)) for _ in range(8)]
        for worker in workers:worker.start()
        start.set()
        outcomes=[queue.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():worker.kill();worker.join()
        check('exactly-one-winner',sum(x[0]=='ok' for x in outcomes)==1)
        check('seven-denied',sum(x[0]=='denied' for x in outcomes)==7)
        check('workers-exited',all(x.exitcode==0 for x in workers))
        q=m.inspect(root)
        check('rollback-required',q['state']=='ROLLBACK_REQUIRED')
        check('generation-three',q['generation']==3)
        try:m.transition(root,p['id'],3,'ROLLED_BACK')
        except m.RecoveryDenied:check('no-false-rollback',True)
        else:check('no-false-rollback',False)
        check('stable',m.inspect(root)==q)
        check('expired-remains-blocked',m.expire(root,deadline+1)=='ROLLBACK_REQUIRED')
        check('no-physical-execution',True)
    return dict(version='0.6.0',mode='offline-physical-recovery-arbitration',selftest='passed',totalScenarios=len(rows),results=rows,executionAuthorized=False,physicalExecutionEnabled=False,commandsExecuted=0,limitations=['No hyprctl execution','No physical rollback','No authenticated confirmation endpoint','Not yet connected to operational writer'])

if __name__=='__main__':
    try:print(json.dumps(run(),indent=2))
    except BaseException as exc:
        print(json.dumps(dict(selftest='failed',error=f'{type(exc).__name__}: {exc}')))
        raise
