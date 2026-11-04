#!/usr/bin/env python3
"""Disposable offline checks for schema-2 lock integration; no display calls."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
p=HERE/'display-fenced-serialized-v0600.py'
s=importlib.util.spec_from_file_location('fenced_integrated',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

def selftest():
    rows=[]
    def check(name,ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    def denied(name,fn):
        try:fn()
        except (RuntimeError,OSError,ValueError):check(name,True)
        else:check(name,False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-fenced-') as tmp:
        r=Path(tmp)/'runtime';j=Path(tmp)/'journal';r.mkdir(mode=0o700);j.mkdir(mode=0o700)
        session=m.FencedJournal(r,j)
        denied('inactive-begin',lambda:session.begin('op','digest'))
        denied('inactive-decision',session.decision)
        with session as active:
            check('active',active is session)
            check('empty-not-safe',active.decision()=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
            initial=active.begin('op','digest')
            check('schema-two',initial['schema']==2)
            check('session-id-length',len(initial['session_id'])==64)
            check('initial-generation',initial['generation']==1)
            check('initial-recovery-required',initial['recovery_required'] is True)
            check('journal-exists',(j/'intent.json').is_file())
            check('serialization-lock-exists',(j/'.intent-serialize.lock').is_file())
            check('owner-lock-exists',(r/m.owner.NAME).is_file())
            denied('duplicate-begin',lambda:active.begin('other','digest'))
            denied('wrong-session',lambda:active.advance('0'*64,1,'applying'))
            denied('stale-generation',lambda:active.advance(initial['session_id'],2,'applying'))
            denied('invalid-edge',lambda:active.advance(initial['session_id'],1,'confirmed'))
            # Separate process cannot obtain serialization lock.
            code='import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);\ntry:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:sys.exit(17)\nsys.exit(0)'.replace('\\n','\n')
            for label,path in [('serialize',j/'.intent-serialize.lock'),('owner',r/m.owner.NAME)]:
                result=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,timeout=5)
                check(label+'-contended',result.returncode==17)
            nxt=active.advance(initial['session_id'],1,'applying')
            check('advanced-generation',nxt['generation']==2)
            check('advanced-phase',nxt['phase']=='applying')
            check('same-session',nxt['session_id']==initial['session_id'])
            check('same-coordinator',nxt['coordinator_identity']==initial['coordinator_identity'])
            check('still-recovery-required',nxt['recovery_required'] is True)
            check('pending-blocked',active.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            denied('reenter-active',session.__enter__)
        denied('closed-advance',lambda:session.advance(initial['session_id'],2,'awaiting_confirmation'))
        denied('closed-begin',lambda:session.begin('again','digest'))
        denied('single-use',session.__enter__)
        with m.FencedJournal(r,j) as restarted:
            check('restart-pending-blocked',restarted.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            denied('restart-cannot-adopt',lambda:restarted.begin('again','digest'))
            denied('old-session-fails-after-restart',lambda:restarted.advance('f'*64,2,'awaiting_confirmation'))
        # Existing legacy schema is never silently migrated.
        legacy=Path(tmp)/'legacy';legacy.mkdir(mode=0o700)
        (legacy/'intent.json').write_text('{"schema":1}')
        (legacy/'intent.json').chmod(0o600)
        with m.FencedJournal(r,legacy) as s2:
            check('legacy-blocked',s2.decision()=='BLOCK_AND_INVESTIGATE')
            denied('legacy-not-adopted',lambda:s2.begin('op','digest'))
        check('legacy-preserved',(legacy/'intent.json').read_text()=='{"schema":1}')
        check('no-physical-execution',True)
        check('no-systemd-execution',True)
    return {'version':'0.6.0','mode':'offline-fenced-serialized-integration','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'persistentFencingIntegrated':False,'limitations':['Schema-2 is integrated with cooperative locks only, not ContinuousSession or runtime workers','No crash durability or physical rollback proof','Existing journals are never automatically reclaimed']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',nargs='?',choices=('selftest',),default='selftest');parser.parse_args()
    try:print(json.dumps(selftest(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':str(exc),'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0}));sys.exit(2)
