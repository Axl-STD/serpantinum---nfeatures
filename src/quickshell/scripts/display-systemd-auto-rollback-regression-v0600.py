#!/usr/bin/env python3
"""Regression for offline auto rollback worker; no systemd or physical calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
p=HERE/'display-systemd-auto-rollback-v0600.py'
spec=importlib.util.spec_from_file_location('auto_worker',p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sim=m.sim
c=m.c
checks=[]

def check(name, value):
    checks.append({'scenario':name,'passed':bool(value)})
    if not value: raise AssertionError(name)

def setup(parent, name):
    root=parent/name
    root.mkdir(mode=0o700)
    sim.initialize(root)
    rec=c.prepare(root,sim.BASE,time.time_ns()+30_000_000_000)
    armed=c.transition(root,rec['id'],rec['generation'],'ARMED')
    return root,rec,armed

try:
    with tempfile.TemporaryDirectory(prefix='serpantinum-auto-rollback-') as td:
        base=Path(td)
        a,rec,armed=setup(base,'expire-applied')
        applied=sim.apply(a,rec['id'],armed['generation'])
        check('applied',applied['state']=='APPLIED')
        check('target-visible',sim.inspect(a)['simulatedMonitor']==sim.TARGET)
        result=m.execute(a,now_ns=rec['deadline_ns'])
        check('expired-auto-restore',result['decision']=='SIMULATED_ROLLBACK_VERIFIED')
        check('terminal-rollback',c.inspect(a)['state']=='ROLLED_BACK')
        check('base-restored',sim.inspect(a)['simulatedMonitor']==sim.BASE)
        check('generation-seven',c.inspect(a)['generation']==7)
        check('idempotent-rollback',m.execute(a)['decision']=='ALREADY_ROLLED_BACK')
        check('idempotent-generation',c.inspect(a)['generation']==7)
        b,rec,armed=setup(base,'confirm')
        applied=sim.apply(b,rec['id'],armed['generation'])
        sim.confirm(b,rec['id'],applied['generation'])
        check('confirmed-terminal',m.execute(b,now_ns=rec['deadline_ns'])['decision']=='CONFIRMED_NO_ROLLBACK')
        check('confirmed-target-preserved',sim.inspect(b)['simulatedMonitor']==sim.TARGET)
        check('confirmed-generation-stable',c.inspect(b)['generation']==5)
        d,rec,armed=setup(base,'not-expired')
        check('not-expired-blocked',m.execute(d,now_ns=rec['deadline_ns']-1)['decision']=='BLOCK_NOT_EXPIRED')
        check('not-expired-armed',c.inspect(d)['state']=='ARMED')
        check('not-expired-base',sim.inspect(d)['simulatedMonitor']==sim.BASE)
        check('missing-blocked',m.execute(base/'absent')['decision']=='BLOCK_AND_INVESTIGATE')
        e,rec,armed=setup(base,'armed-expiry')
        result=m.execute(e,now_ns=rec['deadline_ns'])
        check('armed-expired-restored',result['decision']=='SIMULATED_ROLLBACK_VERIFIED')
        check('armed-expired-base',sim.inspect(e)['simulatedMonitor']==sim.BASE)
        f,rec,armed=setup(base,'uncertain')
        try: sim.apply(f,rec['id'],armed['generation'],fail_after_effect=True)
        except sim.SimulationDenied: pass
        check('uncertain-blocked',m.execute(f,now_ns=rec['deadline_ns'])['decision']=='BLOCK_NOT_EXPIRED')
        check('uncertain-remains',c.inspect(f)['state']=='UNCERTAIN')
        g,rec,armed=setup(base,'tamper')
        sim.apply(g,rec['id'],armed['generation'])
        c.expire(g,now_ns=rec['deadline_ns'])
        (g/'simulated-monitor.json').write_text('{}')
        check('tamper-blocked',m.execute(g)['decision']=='BLOCK_AND_INVESTIGATE')
        check('tamper-not-restored',c.inspect(g)['state']=='ROLLBACK_REQUIRED')
        h,rec,armed=setup(base,'child-process')
        sim.apply(h,rec['id'],armed['generation'])
        c.expire(h,now_ns=rec['deadline_ns'])
        proc=subprocess.run([sys.executable,'-B',str(p),str(h)],capture_output=True,text=True,timeout=10)
        check('child-exit-zero',proc.returncode==0)
        check('child-json-success',json.loads(proc.stdout)['decision']=='SIMULATED_ROLLBACK_VERIFIED')
        check('child-restored',sim.inspect(h)['simulatedMonitor']==sim.BASE)
        check('no-physical-command',all(x not in p.read_text() for x in ('hyprctl','wlr-randr','os.system(')))
        check('physical-disabled',m.execute(h)['physicalExecutionEnabled'] is False)
        check('commands-zero',m.execute(h)['commandsExecuted']==0)
    print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,
        'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':f'{type(exc).__name__}: {exc}',
        'totalScenarios':len(checks),'results':checks},indent=2))
    sys.exit(2)
