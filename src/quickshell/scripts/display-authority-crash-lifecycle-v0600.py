#!/usr/bin/env python3
"""Offline coordinator crash characterization. No display/systemd operations.

Each fixture is a subprocess started by this test and terminated by its PID.
A stale Unix socket after an uncatchable termination is a known GAP, not recovery.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent

def load(name, key):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency: ' + name)
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

session = load('display-authority-session-v0600.py', 'crash_session')
owner = session.owner
serialized = session.serialized

CHILD = r'''
import importlib.util,os,sys,time
from pathlib import Path
p=Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('s',p)
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
r,j,b=map(Path,sys.argv[2:5]); phase=sys.argv[5]
with s.Session(r,j,b):
    item=s.serialized.guard.prepare(j,'crash-fixture','digest')
    if phase=='applying':
        s.serialized.guard.advance(j,1,'applying')
    print('READY',flush=True)
    time.sleep(30)
'''

def check(rows, name, ok, classification='PASS'):
    rows.append({'scenario':name,'passed':bool(ok),'classification':classification})
    if not ok:
        raise AssertionError(name)

def run_one(rows, sig, phase):
    label = signal.Signals(sig).name.lower() + '-' + phase
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-crash-') as d:
        base = Path(d)
        roots = [base / n for n in ('runtime','journal','broker')]
        for root in roots:
            root.mkdir(mode=0o700)
        runtime, journal, broker = roots
        env = dict(os.environ)
        env.pop(owner.ENV, None)
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        proc = subprocess.Popen([sys.executable,'-B','-c',CHILD,
                                 str(HERE/'display-authority-session-v0600.py'),
                                 *(str(x) for x in roots),phase],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                text=True,env=env,start_new_session=True)
        try:
            import select
            ready,_,_ = select.select([proc.stdout],[],[],5)
            marker = proc.stdout.readline().strip() if ready else ''
            check(rows,label+'-fixture-ready',marker=='READY')
            check(rows,label+'-lock-created',(runtime/owner.NAME).is_file())
            check(rows,label+'-socket-created',(broker/'worker.sock').is_socket())
            check(rows,label+'-journal-present',(journal/'intent.json').is_file())
            check(rows,label+'-phase-recorded',serialized.guard._read(journal)['phase']==phase)
            os.kill(proc.pid,sig)
            proc.wait(timeout=5)
            check(rows,label+'-signal-observed',proc.returncode==-sig)
            with owner.owner_only(runtime):
                check(rows,label+'-kernel-lock-released',True)
            check(rows,label+'-journal-still-blocks',
                  serialized.guard.recovery_decision(journal)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check(rows,label+'-stale-socket-retained',(broker/'worker.sock').is_socket(), 'GAP')
            rejected=False
            try:
                with session.Session(runtime,journal,broker):
                    pass
            except (OSError,RuntimeError):
                rejected=True
            check(rows,label+'-new-session-fail-closed',rejected)
            check(rows,label+'-journal-not-deleted',(journal/'intent.json').is_file())
            check(rows,label+'-no-automatic-cleanup',(broker/'worker.sock').exists())
        finally:
            if proc.poll() is None:
                os.kill(proc.pid,signal.SIGKILL)
                proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()

def selftest():
    rows=[]
    for sig in (signal.SIGTERM,signal.SIGKILL):
        for phase in ('prepared','applying'):
            run_one(rows,sig,phase)
    return {'version':'0.6.0','mode':'offline-coordinator-crash-characterization',
            'selftest':'passed','totalScenarios':len(rows),'results':rows,
            'gapCount':sum(x['classification']=='GAP' for x in rows),
            'executionAuthorized':False,'physicalExecutionEnabled':False,
            'authenticatedDelegationImplemented':False,'cleanupAuthorized':False,
            'commandsExecuted':0,
            'limitations':['Stale Unix socket after coordinator SIGTERM/SIGKILL blocks a new session',
                           'Released kernel lock is not logical recovery',
                           'No authenticated lifetime authorization or physical rollback']}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['selftest'])
    parser.parse_args()
    try:
        print(json.dumps(selftest(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':str(exc),'errorType':type(exc).__name__,
                          'physicalExecutionEnabled':False,'commandsExecuted':0}))
        sys.exit(2)
