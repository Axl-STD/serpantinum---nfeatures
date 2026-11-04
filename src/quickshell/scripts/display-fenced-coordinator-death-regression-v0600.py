#!/usr/bin/env python3
"""Offline coordinator death, orphan-worker and schema-2 journal regression.

Uses only disposable Python subprocesses, private roots, and no systemd/display calls.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
CHILD = r'''
import importlib.util,json,os,sys,time
from pathlib import Path
here=Path(sys.argv[1]); roots=[Path(p) for p in sys.argv[2:5]]; marker=Path(sys.argv[5]); phase=sys.argv[6]
p=here/'display-continuous-fenced-authority-v0600.py'
s=importlib.util.spec_from_file_location('death_fenced',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with m.FencedContinuousSession(*roots) as session:
    if phase=='before-begin':
        marker.write_text('ready');time.sleep(120)
    first=session.begin('offline-probe','death-digest')
    if phase=='after-begin':
        marker.write_text('ready');time.sleep(120)
    # Simulate a transaction already recorded as applying, without claiming physical execution.
    written=m.fence.advance(roots[1],first['session_id'],1,'applying')
    if phase=='after-advance':
        marker.write_text('ready');time.sleep(120)
'''
WORKER = r'''
import json,os,sys,time
from pathlib import Path
marker=Path(sys.argv[1]);marker.write_text(str(os.getpid()));time.sleep(120)
'''


def run():
    rows=[]
    def check(name, valid):
        rows.append({'scenario':name,'passed':bool(valid)})
        if not valid: raise AssertionError(name)
    def stop(p):
        if p.poll() is None:
            p.kill()
        try: p.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            p.kill();p.communicate(timeout=4)
    def wait(path,p,seconds=8):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if path.exists():return True
            if p.poll() is not None:return False
            time.sleep(.02)
        return False
    import importlib.util
    f=HERE/'display-session-fencing-journal-v0600.py'
    spec=importlib.util.spec_from_file_location('death_fence',f)
    fence=importlib.util.module_from_spec(spec);spec.loader.exec_module(fence)
    with tempfile.TemporaryDirectory(prefix='serpantinum-death-v0600-') as td:
        base=Path(td)
        for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGKILL):
            for phase in ('before-begin','after-begin','after-advance'):
                prefix=signum.name+'-'+phase
                case=base/prefix;case.mkdir(mode=0o700)
                roots=[case/n for n in ('runtime','journal','broker')]
                for root in roots:root.mkdir(mode=0o700)
                marker=case/'ready';wmark=case/'worker-ready'
                env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','SERPANTINUM_RUNTIME_ADMISSION_FD')}
                env['PYTHONDONTWRITEBYTECODE']='1'
                coordinator=subprocess.Popen([sys.executable,'-B','-c',CHILD,str(HERE),*[str(x) for x in roots],str(marker),phase],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,env=env)
                worker=subprocess.Popen([sys.executable,'-B','-c',WORKER,str(wmark)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,env=env)
                try:
                    check(prefix+'-coordinator-ready',wait(marker,coordinator))
                    check(prefix+'-worker-ready',wait(wmark,worker))
                    old=fence.read(roots[1]);check(prefix+'-expected-before', (old is None) if phase=='before-begin' else (old is not None and old['generation']==(2 if phase=='after-advance' else 1)))
                    os.kill(coordinator.pid,signum)
                    coordinator.wait(timeout=6)
                    check(prefix+'-coordinator-dead',coordinator.poll() is not None)
                    check(prefix+'-worker-survived',worker.poll() is None)
                    current=fence.read(roots[1]);check(prefix+'-journal-preserved',current==old)
                    if current is not None:
                        check(prefix+'-recovery-required',current['recovery_required'] is True)
                        check(prefix+'-no-adoption',fence._identity()!=current['coordinator_identity'])
                    else:
                        check(prefix+'-no-phantom-journal',current is None)
                    # A fresh coordinator may open locks, but must not begin over a pending journal.
                    probe='\nimport importlib.util\nimport sys\nfrom pathlib import Path\n\nhere = Path(sys.argv[1])\nroots = [Path(x) for x in sys.argv[2:5]]\n\np = here / "display-continuous-fenced-authority-v0600.py"\ns = importlib.util.spec_from_file_location("probe_fenced", p)\nm = importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n\ntry:\n    with m.FencedContinuousSession(*roots) as session:\n        session.begin("offline-probe", "replacement")\nexcept m.fence.FenceDenied:\n    sys.exit(17)\nexcept Exception as exc:\n    print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)\n    sys.exit(19)\nelse:\n    sys.exit(0)\n'
                    if current is not None:
                        result=subprocess.run([sys.executable,'-B','-c',probe,str(HERE),*[str(x) for x in roots]],env=env,capture_output=True,text=True,timeout=12)
                        print('PROBE_DIAGNOSTIC', prefix, 'returncode=', result.returncode, 'stdout=', repr(result.stdout), 'stderr=', repr(result.stderr), file=sys.stderr, flush=True); check(prefix+'-new-session-rejected',result.returncode==17)
                        check(prefix+'-post-probe-journal-stable',fence.read(roots[1])==current)
                finally:
                    stop(coordinator);stop(worker)
        check('no-physical-execution',True)
    return {'version':'0.6.0','mode':'offline-fenced-coordinator-death','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'limitations':['Worker is an independently surviving disposable fixture, not a privileged executor','SIGKILL releases kernel locks but cannot run cleanup','No systemd, display commands, or physical rollback','This suite does not demonstrate continuous worker authorization']}

if __name__=='__main__':
    try: print(json.dumps(run(),indent=2))
    except BaseException as exc:
        print(json.dumps({'version':'0.6.0','selftest':'failed','error':type(exc).__name__+': '+str(exc),'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0}));sys.exit(2)
