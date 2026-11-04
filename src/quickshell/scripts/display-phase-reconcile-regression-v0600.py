#!/usr/bin/env python3
"""Offline fault-state matrix for simulated-only reconciliation."""
import importlib.util
import json
import tempfile
import time
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('rec',HERE/'display-phase-reconcile-v0600.py')
rec=importlib.util.module_from_spec(spec);spec.loader.exec_module(rec)
c=rec.c;sim=rec.sim;phase=rec.phase;temporal=rec.temporal
results=[]
def check(label, condition):
    if not condition:raise AssertionError(label)
    results.append({'scenario':label,'passed':True})
def setup(root):
    root.mkdir(mode=0o700)
    sim.initialize(root)
    r=c.prepare(root,sim.BASE,time.time_ns()+30_000_000_000)
    a=c.transition(root,r['id'],r['generation'],'ARMED')
    applied=sim.apply(root,r['id'],a['generation'])
    temporal.bind(root)
    return applied

def case(kind):
    with tempfile.TemporaryDirectory(prefix='serpantinum-reconcile-') as d:
        root=Path(d)/'transaction';r=setup(root)
        with c._locked(root) as path:
            r=c._read(path)
            if kind=='no-journal':pass
            else:
                phase.write(path,r,'ROLLBACK_INTENT',sim.TARGET)
                if kind not in ('pending',):
                    r=dict(r,state='UNCERTAIN',generation=r['generation']+1);c._write(path,r)
                    if kind not in ('target',):sim._write_observed(path,sim.BASE)
                    if kind in ('effect','effect-bad','terminal','completed','terminal-bad'):
                        phase.write(path,r,'ROLLBACK_EFFECT_OBSERVED',sim.BASE)
                    if kind in ('terminal','completed','terminal-bad'):
                        r=dict(r,state='ROLLED_BACK',generation=r['generation']+1);c._write(path,r)
                    if kind=='completed':phase.write(path,r,'COMPLETED',sim.BASE)
                    if kind=='wrong-phase':phase.write(path,r,'APPLY_EFFECT_OBSERVED',sim.BASE)
                    if kind=='wrong-generation':
                        phase.write(path,dict(r,generation=r['generation']+3),'ROLLBACK_EFFECT_OBSERVED',sim.BASE)
                    if kind=='wrong-digest':phase.write(path,r,'ROLLBACK_EFFECT_OBSERVED',sim.TARGET)
                    if kind=='effect-bad':phase.write(path,r,'ROLLBACK_EFFECT_OBSERVED',sim.TARGET)
                    if kind=='terminal-bad':phase.write(path,r,'ROLLBACK_INTENT',sim.TARGET)
                    if kind=='wrong-id':phase.write(path,dict(r,id='f'*64),'ROLLBACK_EFFECT_OBSERVED',sim.BASE)
        before=c.inspect(root)
        result=rec.execute(root)
        decision=result['decision']
        if kind in ('intent','effect'):
            check(kind+':reconciled',decision=='SIMULATED_RECONCILIATION_VERIFIED')
            check(kind+':terminal',c.inspect(root)['state']=='ROLLED_BACK')
            check(kind+':journal',phase.audit(root)['decision']=='TERMINAL_VERIFIED')
            check(kind+':idempotent',rec.execute(root)['decision']=='TERMINAL_VERIFIED')
        elif kind=='terminal':
            check(kind+':repaired',decision=='TERMINAL_JOURNAL_REPAIRED')
            check(kind+':journal',phase.audit(root)['decision']=='TERMINAL_VERIFIED')
            check(kind+':generation',c.inspect(root)['generation']==before['generation'])
        elif kind=='completed':
            check(kind+':verified',decision=='TERMINAL_VERIFIED')
            check(kind+':unchanged',c.inspect(root)==before)
        else:
            check(kind+':blocked',decision.startswith('BLOCK_'))
            check(kind+':unchanged',c.inspect(root)==before)
        check(kind+':no-physical',result['commandsExecuted']==0 and result['physicalExecutionEnabled'] is False)

def run():
    for kind in ('intent','effect','terminal','completed','no-journal','pending','target','wrong-phase','wrong-generation','wrong-digest','effect-bad','terminal-bad','wrong-id'):
        case(kind)
    return dict(version='0.6.0',selftest='passed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,commandsExecuted=0)
if __name__=='__main__':print(json.dumps(run(),indent=2))
