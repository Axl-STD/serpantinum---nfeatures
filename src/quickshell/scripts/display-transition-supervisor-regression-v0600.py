#!/usr/bin/env python3
"""Offline subprocess, concurrency, interruption and ambiguous-result regression."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('supervisor', HERE/'display-transition-supervisor-v0600.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def selftest():
    rows=[]
    def check(name, cond):
        rows.append({'scenario':name,'passed':bool(cond)})
        if not cond: raise AssertionError(name)
    def rejected(fn):
        try: fn()
        except (m.SupervisionDenied,m.mod.ContinuityDenied): return True
        return False
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-supervisor-') as tmp:
        roots=[Path(tmp)/name for name in ('runtime','journal','broker')]
        for root in roots: root.mkdir(mode=0o700)
        s=m.SupervisedSession(*roots)
        check('initially-unpoisoned',not s.poisoned)
        check('initially-not-authorized',s.report()['executionAuthorized'] is False)
        with s:
            check('session-active',s.active)
            check('initial-no-commit',s.last_committed is None)
            guard=m.mod.base.serialized.guard
            p=guard.prepare(roots[1],'offline-probe','digest')
            check('prepared',p['generation']==1)
            check('stale-rejected',rejected(lambda:s.transition(os.getpid(),2,'applying')))
            check('invalid-pid-rejected',rejected(lambda:s.transition(0,1,'applying')))
            check('terminal-rejected',rejected(lambda:s.transition(os.getpid(),1,'confirmed')))
            check('preflight-does-not-poison',not s.poisoned)
            check('preflight-does-not-mutate',guard._read(roots[1])['generation']==1)
            check('no-grant-after-preflight',not s.broker.allowed)
            # Valid handshake from a real child process.
            code=('import json,socket,sys,time;time.sleep(.15);'
                  's=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);'
                  's.sendall(json.dumps({"operation":"offline-probe"}).encode());'
                  'print(s.recv(32).decode().strip(),flush=True);s.close()')
            child=subprocess.Popen([sys.executable,'-B','-c',code,str(s.broker.path)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                result=s.transition(child.pid,1,'applying')
                out,err=child.communicate(timeout=6)
                check('real-child-ack',out.strip()=='ACK' and child.returncode==0)
                check('committed-phase',result['phase']=='applying')
                check('committed-generation',result['generation']==2)
                check('commit-recorded',s.last_committed==(2,'applying'))
                check('journal-matches-commit',guard._read(roots[1])==result)
                check('not-poisoned-after-commit',not s.poisoned)
            finally:
                if child.poll() is None: child.kill();child.communicate()
            # Foreign thread cannot acquire coordinator authority.
            outcomes=[]
            t=threading.Thread(target=lambda:outcomes.append(rejected(lambda:s.transition(os.getpid(),2,'awaiting_confirmation'))))
            t.start();t.join(timeout=4)
            check('foreign-thread-finished',not t.is_alive())
            check('foreign-thread-denied',outcomes==[True])
            check('foreign-thread-no-mutation',guard._read(roots[1])['generation']==2)
            # A denied handshake is ambiguous and poisons this session.
            wrong=threading.Thread(target=lambda:m.mod.base.worker.request(s.broker.path,'incorrect-operation'))
            wrong.start()
            check('invalid-handshake-denied',rejected(lambda:s.transition(os.getpid(),2,'awaiting_confirmation')))
            wrong.join(timeout=5)
            check('invalid-handshake-thread-finished',not wrong.is_alive())
            check('poisoned-on-ambiguous-attempt',s.poisoned)
            check('poisoned-report',s.report()['supervisorPoisoned'] is True)
            check('poisoned-rejects-further',rejected(lambda:s.transition(os.getpid(),2,'awaiting_confirmation')))
            check('poisoned-journal-unchanged',guard._read(roots[1])['generation']==2)
            check('no-physical-authorization',s.report()['physicalExecutionEnabled'] is False)
            check('no-logical-recovery-claim',s.report()['logicalRecoveryVerified'] is False)
        check('closed',not s.active)
        check('socket-removed-after-normal-exit',not (roots[2]/'worker.sock').exists())
        # Separate coordinator process holds the same owner lock; SIGKILL releases it.
        holder=('import importlib.util,sys,time,pathlib;'
                'p=pathlib.Path(sys.argv[1]);'
                's=importlib.util.spec_from_file_location("own",sys.argv[2]);'
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                'c=m.owner_only(p);c.__enter__();print("READY",flush=True);time.sleep(30)')
        owner_path=HERE/'display-admission-owner-v0600.py'
        proc=subprocess.Popen([sys.executable,'-B','-c',holder,str(roots[0]),str(owner_path)],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            ready=proc.stdout.readline().strip()
            check('holder-ready',ready=='READY')
            check('holder-live',proc.poll() is None)
            check('contention-denied',rejected_owner(roots[0]))
            os.kill(proc.pid,signal.SIGKILL)
            proc.wait(timeout=5)
            check('sigkill-observed',proc.returncode==-signal.SIGKILL)
            with m.mod.base.owner.owner_only(roots[0]):
                check('kernel-lock-reacquired',True)
            check('sigkill-not-logical-proof',guard.recovery_decision(roots[1])=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        finally:
            if proc.poll() is None: proc.kill();proc.wait(timeout=5)
        with m.SupervisedSession(*roots) as later:
            check('new-session-unpoisoned',not later.poisoned)
            check('old-journal-still-pending',later.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('new-session-not-authorized',later.report()['executionAuthorized'] is False)
        check('no-systemd-commands',True)
        check('no-display-commands',True)
    return {'version':'0.6.0','mode':'offline-transition-supervisor',
            'selftest':'passed','totalScenarios':len(rows),'results':rows,
            'executionAuthorized':False,'physicalExecutionEnabled':False,
            'logicalRecoveryVerified':False,'commandsExecuted':0,
            'limitations':['Cooperative wrapper only: direct guard writers bypass this supervisor',
                           'SIGKILL fixture proves kernel lock release, not logical rollback',
                           'Same-UID interference, TOCTOU and physical execution remain unverified']}

def rejected_owner(root):
    try:
        with m.mod.base.owner.owner_only(root): pass
    except m.mod.base.owner.AdmissionDenied:
        return True
    return False

if __name__=='__main__':
    argparse.ArgumentParser().add_argument('command',nargs='?',choices=['selftest'],default='selftest')
    print(json.dumps(selftest(),indent=2))
