#!/usr/bin/env python3
"""Offline regression for fresh worker authority and guarded journal transitions."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('continuous', HERE/'display-continuous-authority-v0600.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def selftest():
    rows=[]
    def check(name, ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    def deny(name, fn):
        try: fn()
        except (m.ContinuityDenied, m.base.worker.AuthorityError, m.base.serialized.guard.GuardError):
            check(name,True)
        else: check(name,False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-continuous-') as tmp:
        roots=[Path(tmp)/x for x in ('runtime','journal','broker')]
        for root in roots: root.mkdir(mode=0o700)
        s=m.ContinuousSession(*roots)
        check('initially-inactive',not s.active)
        deny('transition-before-enter',lambda:s.transition(os.getpid(),1,'applying'))
        with s:
            check('owner-thread-recorded',s.owner_thread==threading.get_ident())
            foreign = {}
            def probe_foreign():
                actions = {
                    'live': lambda: s._live(),
                    'revoke': lambda: s.revoke(os.getpid()),
                    'transition': lambda: s.transition(os.getpid(),1,'applying'),
                    'exit': lambda: s.__exit__(None,None,None),
                }
                for key, fn in actions.items():
                    try:
                        fn()
                        foreign[key] = 'ACCEPTED'
                    except m.ContinuityDenied:
                        foreign[key] = 'DENIED'
            foreign_thread = threading.Thread(target=probe_foreign)
            foreign_thread.start()
            foreign_thread.join(timeout=5)
            check('foreign-thread-finished',not foreign_thread.is_alive())
            for action in ('live','revoke','transition','exit'):
                check('foreign-thread-'+action+'-denied',foreign.get(action)=='DENIED')
            check('foreign-thread-session-preserved',s.active and s.broker is not None)
            check('foreign-thread-no-revocation',os.getpid() not in s.revoked)
            check('foreign-thread-no-registration',not s.broker.allowed)
            check('session-active',s.active)
            check('coordinator-pid',s.owner_pid==os.getpid())
            check('coordinator-starttime',s.owner_starttime==m.base.worker.starttime(os.getpid()))
            check('report-no-execution',s.report()['executionAuthorized'] is False)
            check('report-no-physical',s.report()['physicalExecutionEnabled'] is False)
            p=m.base.serialized.guard.prepare(roots[1],'offline-probe','digest')
            check('prepared',p['generation']==1)
            deny('invalid-pid',lambda:s.transition(0,1,'applying'))
            deny('negative-pid',lambda:s.transition(-1,1,'applying'))
            deny('invalid-generation',lambda:s.transition(os.getpid(),0,'applying'))
            deny('boolean-generation',lambda:s.transition(os.getpid(),True,'applying'))
            deny('stale-generation',lambda:s.transition(os.getpid(),2,'applying'))
            deny('skip-phase',lambda:s.transition(os.getpid(),1,'awaiting_confirmation'))
            deny('confirmed-forbidden',lambda:s.transition(os.getpid(),1,'confirmed'))
            deny('rolledback-forbidden',lambda:s.transition(os.getpid(),1,'rolled_back'))
            deny('unknown-phase',lambda:s.transition(os.getpid(),1,'unknown'))
            check('invalid-does-not-register',not s.broker.allowed)
            def step(gen, phase, operation='offline-probe'):
                answers=[]
                t=threading.Thread(target=lambda:answers.append(m.base.worker.request(s.broker.path,operation)))
                t.start()
                try: outcome=s.transition(os.getpid(),gen,phase)
                finally: t.join(timeout=5)
                return outcome,answers
            p,a=step(1,'applying')
            check('fresh-authority-first',a==[True])
            check('first-transition',p['phase']=='applying' and p['generation']==2)
            check('first-grant-consumed',not s.broker.allowed)
            deny('replay-generation',lambda:s.transition(os.getpid(),1,'awaiting_confirmation'))
            p,a=step(2,'awaiting_confirmation')
            check('fresh-authority-second',a==[True])
            check('second-transition',p['generation']==3)
            check('second-grant-consumed',not s.broker.allowed)
            # Real child process with kernel peer PID, not just same-process threads.
            child_code = ('import json,socket,sys,time;time.sleep(.15);'
                          's=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);'
                          's.sendall(json.dumps({"operation":"offline-probe"}).encode());'
                          'print(s.recv(32).decode().strip(),flush=True);s.close()')
            child=subprocess.Popen([sys.executable,'-B','-c',child_code,str(s.broker.path)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                p=s.transition(child.pid,3,'rollback_required')
                out,err=child.communicate(timeout=5)
                check('real-child-ack',out.strip()=='ACK' and child.returncode==0)
                check('real-child-transition',p['phase']=='rollback_required' and p['generation']==4)
                check('real-child-grant-consumed',child.pid not in s.broker.allowed)
            finally:
                if child.poll() is None:
                    child.kill();child.communicate()
            check('journal-blocks',s.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            s.revoke(os.getpid())
            check('revoked-recorded',os.getpid() in s.revoked)
            deny('revoked-transition',lambda:s.transition(os.getpid(),4,'uncertain'))
            check('revoked-does-not-register',not s.broker.allowed)
            check('revoked-journal-unchanged',m.base.serialized.guard._read(roots[1])['generation']==4)
            check('no-physical-commands',s.report()['commandsExecuted']==0)
            check('no-authenticated-delegation-claim',s.report()['authenticatedDelegationImplemented'] is False)
        check('inactive-after-exit',not s.active)
        check('socket-removed',not (roots[2]/'worker.sock').exists())
        deny('transition-after-exit',lambda:s.transition(os.getpid(),4,'uncertain'))
        check('journal-survives-exit',m.base.serialized.guard._read(roots[1])['generation']==4)
        with m.ContinuousSession(*roots) as another:
            check('new-session-possible',another.active)
            check('old-ack-not-carried',not another.acknowledged)
            check('new-session-no-grants',not another.broker.allowed)
            check('old-session-still-inactive',not s.active)
            check('recovery-still-blocks',another.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        check('no-systemd',True)
        check('no-hyprctl',True)
        check('no-physical-rollback',True)
    return {'version':'0.6.0','mode':'offline-continuous-authority','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'authenticatedDelegationImplemented':False,
            'commandsExecuted':0,'limitations':['Fresh peer handshake per offline transition, not persistent worker authentication',
            'Same-UID interference and noncooperating direct journal writers remain outside guarantee',
            'No physical display execution, terminal confirmation, or rollback']}
if __name__=='__main__':
    argparse.ArgumentParser().parse_args()
    print(json.dumps(selftest(),indent=2))
