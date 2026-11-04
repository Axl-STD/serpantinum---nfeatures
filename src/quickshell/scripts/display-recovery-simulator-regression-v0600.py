#!/usr/bin/env python3
"""Regression for simulated controller: no real display operations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('recovery_sim', HERE / 'display-recovery-simulator-v0600.py')
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)
c = sim.controller
results=[]

def check(name, value):
    results.append({'scenario':name,'passed':bool(value)})
    if not value:
        raise AssertionError(name)

def denied(name, fn):
    try:
        fn()
    except (sim.SimulationDenied, c.RecoveryDenied):
        check(name, True)
    else:
        check(name, False)

def root(td, name):
    path=Path(td)/name
    path.mkdir(mode=0o700)
    return path

def prepare(path, seconds=60):
    sim.initialize(path)
    record=c.prepare(path, sim.BASE, time.time_ns()+seconds*1_000_000_000)
    return c.transition(path, record['id'], 1, 'ARMED')

try:
    with tempfile.TemporaryDirectory(prefix='serpantinum-recovery-sim-') as td:
        a=root(td,'confirm')
        rec=prepare(a)
        check('armed',rec['state']=='ARMED' and rec['generation']==2)
        denied('wrong-token-apply',lambda:sim.apply(a,'0'*64,2))
        denied('stale-apply',lambda:sim.apply(a,rec['id'],1))
        denied('expired-apply',lambda:sim.apply(a,rec['id'],2,now_ns=rec['deadline_ns']))
        check('unchanged-before-apply',sim.inspect(a)['simulatedMonitor']==sim.BASE)
        applied=sim.apply(a,rec['id'],2)
        check('applied',applied['state']=='APPLIED')
        check('applied-generation',applied['generation']==4)
        check('observed-target',sim.inspect(a)['simulatedMonitor']==sim.TARGET)
        denied('repeat-apply',lambda:sim.apply(a,rec['id'],2))
        denied('wrong-confirm-token',lambda:sim.confirm(a,'0'*64,4))
        denied('stale-confirm',lambda:sim.confirm(a,rec['id'],3))
        denied('expired-confirm',lambda:sim.confirm(a,rec['id'],4,now_ns=rec['deadline_ns']))
        confirmed=sim.confirm(a,rec['id'],4)
        check('confirmed',confirmed['state']=='CONFIRMED' and confirmed['generation']==5)
        denied('repeat-confirm',lambda:sim.confirm(a,rec['id'],5))
        denied('rollback-confirmed',lambda:sim.rollback(a,rec['id'],5))
        check('confirmed-observed',sim.inspect(a)['simulatedMonitor']==sim.TARGET)

        b=root(td,'rollback')
        rec=prepare(b)
        applied=sim.apply(b,rec['id'],2)
        check('rollback-applied',applied['state']=='APPLIED')
        check('expired-to-rollback-required',c.expire(b,now_ns=rec['deadline_ns'])=='ROLLBACK_REQUIRED')
        pending=c.inspect(b)
        check('rollback-required-generation',pending['generation']==5)
        denied('late-confirm',lambda:sim.confirm(b,rec['id'],5))
        denied('wrong-rollback-token',lambda:sim.rollback(b,'0'*64,5))
        denied('stale-rollback',lambda:sim.rollback(b,rec['id'],4))
        rolled=sim.rollback(b,rec['id'],5)
        check('rolled-back',rolled['state']=='ROLLED_BACK' and rolled['generation']==7)
        check('restored-observed',sim.inspect(b)['simulatedMonitor']==sim.BASE)
        denied('repeat-rollback',lambda:sim.rollback(b,rec['id'],7))
        denied('late-apply',lambda:sim.apply(b,rec['id'],7))

        d=root(td,'fail-apply')
        rec=prepare(d)
        denied('injected-apply-failure',lambda:sim.apply(d,rec['id'],2,fail_after_effect=True))
        check('apply-failure-uncertain',c.inspect(d)['state']=='UNCERTAIN')
        check('apply-failure-effect-visible',sim.inspect(d)['simulatedMonitor']==sim.TARGET)
        denied('uncertain-no-confirm',lambda:sim.confirm(d,rec['id'],3))
        denied('uncertain-no-reapply',lambda:sim.apply(d,rec['id'],3))
        check('uncertain-stable',c.inspect(d)['state']=='UNCERTAIN')

        e=root(td,'fail-rollback')
        rec=prepare(e)
        sim.apply(e,rec['id'],2)
        c.expire(e,now_ns=rec['deadline_ns'])
        denied('injected-rollback-failure',lambda:sim.rollback(e,rec['id'],5,fail_after_effect=True))
        check('rollback-failure-uncertain',c.inspect(e)['state']=='UNCERTAIN')
        check('rollback-effect-visible',sim.inspect(e)['simulatedMonitor']==sim.BASE)
        denied('uncertain-no-rollback',lambda:sim.rollback(e,rec['id'],6))

        f=root(td,'tamper')
        rec=prepare(f)
        (f/'simulated-monitor.json').write_text('{}')
        denied('tampered-monitor-blocks-apply',lambda:sim.apply(f,rec['id'],2))
        check('tampered-no-state-advance',c.inspect(f)['state']=='ARMED')

        g=root(td,'symlink')
        (g/'simulated-monitor.json').symlink_to('/dev/null')
        denied('symlink-init-rejected',lambda:sim.initialize(g))
        check('symlink-not-followed',(g/'simulated-monitor.json').is_symlink())

        h=root(td,'double-init')
        sim.initialize(h)
        denied('double-init',lambda:sim.initialize(h))
        denied('controller-legacy-applied-still-denied',lambda: c.transition(b,rec['id'],2,'APPLIED'))
        check('offline-only',sim.inspect(b)['physicalExecutionEnabled'] is False and sim.inspect(b)['commandsExecuted']==0)
        check('no-physical-api',all(x not in Path(sim.__file__).read_text() for x in ('subprocess.run(', 'os.system(', 'subprocess.Popen(')))
    output={'version':'0.6.0','mode':'offline-simulated-recovery-arbitration','selftest':'passed',
            'totalScenarios':len(results),'results':results,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'commandsExecuted':0,
            'limitations':['Simulated monitor only; no physical display restoration',
                           'Crash after intent but before effect leaves UNCERTAIN',
                           'No physical watchdog or TTY recovery',
                           'Same-UID hostile pathname races not excluded']}
    print(json.dumps(output,indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),
                      'totalScenarios':len(results),'results':results},indent=2))
    sys.exit(2)
