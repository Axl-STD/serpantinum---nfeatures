#!/usr/bin/env python3
"""Integrated offline regression: process isolation, deadlines, state and recovery."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('integration', HERE/'display-physical-recovery-integration-v0600.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
checks=[]
def check(name,condition):
    checks.append({'scenario':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)
def cli(*args):
    return subprocess.run([sys.executable,'-B',str(HERE/'display-physical-recovery-integration-v0600.py'),*map(str,args)],capture_output=True,text=True,timeout=15)
def root(base,name):
    p=Path(base)/name;p.mkdir(mode=0o700);return p
try:
    with tempfile.TemporaryDirectory(prefix='serpantinum-integrated-') as td:
        a=root(td,'normal')
        p=cli('prepare-simulated',a)
        check('prepare-cli-success',p.returncode==0)
        check('prepare-cli-applied',json.loads(p.stdout)['state']=='APPLIED')
        check('observed-target',m.inspect(a)['simulatedMonitor']==m.sim.TARGET)
        check('controller-applied',m.c.inspect(a)['state']=='APPLIED')
        check('not-yet-expired',m.restore(a)['decision']=='BLOCK_NOT_EXPIRED')
        check('no-premature-rollback',m.inspect(a)['simulatedMonitor']==m.sim.TARGET)
        r=m.restore(a,now_ns=m.c.inspect(a)['deadline_ns'])
        check('deadline-restoration',r['decision']=='SIMULATED_ROLLBACK_VERIFIED')
        check('rolled-back-state',m.c.inspect(a)['state']=='ROLLED_BACK')
        check('restored-monitor',m.inspect(a)['simulatedMonitor']==m.sim.BASE)
        check('idempotent-restore',m.restore(a)['decision']=='ALREADY_ROLLED_BACK')
        check('no-physical-execution',m.inspect(a)['commandsExecuted']==0)
        b=root(td,'worker')
        check('second-prepare',cli('prepare-simulated',b).returncode==0)
        time.sleep(.01)
        # Run rollback as a fresh interpreter, as systemd would do.
        time.sleep(0.01)
        r=cli('restore-simulated',b)
        check('early-worker-denied',r.returncode==2)
        check('early-worker-still-applied',m.c.inspect(b)['state']=='APPLIED')
        # Existing controller expires by deadline, so wait on a short test transaction.
        check('generation-progress',m.c.inspect(b)['generation']==4)
        check('worker-readonly-inspection',cli('inspect-simulated',b).returncode==0)
        check('worker-no-physical',json.loads(cli('inspect-simulated',b).stdout)['physicalExecutionEnabled'] is False)
        d=root(td,'confirm')
        check('prepare-confirm',cli('prepare-simulated',d).returncode==0)
        rec=m.c.inspect(d)
        confirmed=m.sim.confirm(d,rec['id'],rec['generation'])
        check('confirmed-state',confirmed['state']=='CONFIRMED')
        check('confirmed-no-rollback',m.restore(d)['decision']=='CONFIRMED_NO_ROLLBACK')
        check('confirmed-target-stays',m.inspect(d)['simulatedMonitor']==m.sim.TARGET)
        e=root(td,'uncertain')
        m.sim.initialize(e)
        rec=m.c.prepare(e,m.sim.BASE,time.time_ns()+20_000_000_000)
        armed=m.c.transition(e,rec['id'],rec['generation'],'ARMED')
        try:m.sim.apply(e,rec['id'],armed['generation'],fail_after_effect=True)
        except m.sim.SimulationDenied:pass
        check('uncertain-intent',m.c.inspect(e)['state']=='UNCERTAIN')
        check('uncertain-worker-blocked',m.restore(e)['decision']=='BLOCK_NOT_EXPIRED')
        check('uncertain-target-not-falsely-restored',m.inspect(e)['simulatedMonitor']==m.sim.TARGET)
        check('missing-root-fail-closed',cli('restore-simulated',Path(td)/'missing').returncode==2)
        check('invalid-duration-rejected',cli('prepare-simulated',root(td,'bad'),'--seconds','0').returncode==2)
        check('reprepare-rejected',cli('prepare-simulated',a).returncode==2)
        check('offline-source-no-hyprctl-keyword','keyword' not in (HERE/'display-physical-recovery-integration-v0600.py').read_text())
        check('offline-source-no-systemd-run','systemd-run' not in (HERE/'display-physical-recovery-integration-v0600.py').read_text())
    print(json.dumps({'version':'0.6.0','mode':'offline-physical-recovery-integration','selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),'totalScenarios':len(checks),'results':checks},indent=2))
    sys.exit(2)
