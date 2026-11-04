#!/usr/bin/env python3
"""Offline regression for ambiguity classifier. Never calls display APIs."""
import importlib.util,json,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'display-recovery-ambiguity-audit-v0600.py'
s=importlib.util.spec_from_file_location('audit',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
sim=m.sim; temporal=m.temporal
results=[]
def check(name,fn):
    fn();results.append({'scenario':name,'passed':True})
def setup():
    d=tempfile.TemporaryDirectory(prefix='serpantinum-ambiguity-')
    root=Path(d.name);root.chmod(0o700)
    sim.initialize(root)
    r=sim.controller.prepare(root,sim.BASE,time.time_ns()+30_000_000_000)
    temporal.bind(root)
    return d,root,r
def state(root,old,new):
    with sim.controller._locked(root) as locked:
        r=sim.controller._read(locked)
        assert r['state']==old
        sim.controller._write(locked,dict(r,state=new,generation=r['generation']+1))
def eq(root,decision,mode):
    a=m.audit(root)
    assert a['decision']==decision,(a,decision)
    assert a['observedMode']==mode,a
    assert a['commandsExecuted']==0 and a['physicalExecutionEnabled'] is False
    assert a['executionAuthorized'] is False and a['rollbackPhysicalVerified'] is False
for mode in ('BASE','TARGET'):
    for repeat in range(5):
        def case(mode=mode):
            d,root,r=setup()
            try:
                state(root,'PREPARED','UNCERTAIN')
                if mode=='TARGET':sim._write_observed(root,sim.TARGET)
                before=(root/'recovery.json').read_bytes()
                eq(root,'RECONCILIATION_REQUIRED_PHASE_UNKNOWN',mode)
                assert (root/'recovery.json').read_bytes()==before
            finally:d.cleanup()
        check('uncertain-'+mode+'-'+str(repeat),case)
for repeat in range(4):
    def case():
        d,root,r=setup()
        try:
            state(root,'PREPARED','ROLLBACK_REQUIRED')
            eq(root,'RECOVERY_PENDING_NOT_EXECUTED','BASE')
        finally:d.cleanup()
    check('pending-'+str(repeat),case)
for repeat in range(4):
    def case():
        d,root,r=setup()
        try:
            state(root,'PREPARED','ROLLED_BACK')
            eq(root,'TERMINAL_SIMULATED_VERIFIED','BASE')
        finally:d.cleanup()
    check('terminal-'+str(repeat),case)
for repeat in range(4):
    def case():
        d,root,r=setup()
        try:
            state(root,'PREPARED','ROLLED_BACK')
            sim._write_observed(root,sim.TARGET)
            eq(root,'BLOCK_TERMINAL_EFFECT_MISMATCH','TARGET')
        finally:d.cleanup()
    check('terminal-mismatch-'+str(repeat),case)
print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}))
