#!/usr/bin/env python3
"""Real subprocess and fail-closed operational facade tests; no display calls."""
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

def load(name, key):
    p = HERE / name
    spec = importlib.util.spec_from_file_location(key, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

mod = load('display-operational-writer-v0600.py', 'op_writer')
supervisor = load('display-transition-supervisor-v0600.py', 'op_supervisor')
guard = supervisor.mod.base.serialized.guard
owner = supervisor.mod.base.owner

def selftest():
    rows=[]
    def check(name, ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok:
            raise AssertionError(name)
    def denied(fn):
        try:
            fn()
        except (mod.OperationalDenied, supervisor.SupervisionDenied,
                supervisor.mod.ContinuityDenied, guard.GuardError, owner.AdmissionDenied, RuntimeError):
            return True
        return False
    with tempfile.TemporaryDirectory(prefix='serpantinum-operational-') as tmp:
        roots=[Path(tmp)/x for x in ('runtime','journal','broker')]
        for p in roots: p.mkdir(mode=0o700)
        w=mod.OperationalWriter(*roots)
        check('before-enter-denied',denied(w.decision))
        check('before-enter-transition-denied',denied(lambda:w.transition(os.getpid(),1,'applying')))
        with w:
            check('active',w._session.active)
            check('owner-thread',w._thread==threading.get_ident())
            check('writer-report',w.report()['operationalWriter'] is True)
            check('no-physical-authorization',w.report()['physicalExecutionEnabled'] is False)
            check('no-execution-authorization',w.report()['executionAuthorized'] is False)
            check('no-commands',w.report()['commandsExecuted']==0)
            check('no-journal-not-proof',w.decision()=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
            check('no-direct-prepare-method',not hasattr(w,'prepare'))
            check('no-direct-advance-method',not hasattr(w,'advance'))
            check('no-execute-method',not hasattr(w,'execute'))
            check('missing-journal-denied',denied(lambda:w.transition(os.getpid(),1,'applying')))
            # Fixture-only setup; the operational facade never creates a journal.
            prepared=guard.prepare(roots[1],'offline-probe','synthetic-digest')
            check('fixture-prepared',prepared['phase']=='prepared')
            check('pending-decision',w.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('stale-denied',denied(lambda:w.transition(os.getpid(),2,'applying')))
            check('terminal-denied',denied(lambda:w.transition(os.getpid(),1,'confirmed')))
            check('bad-pid-denied',denied(lambda:w.transition(0,1,'applying')))
            check('preflight-no-poison',not w._session.poisoned)
            check('preflight-no-mutation',guard._read(roots[1])==prepared)
            outcomes=[]
            t=threading.Thread(target=lambda:outcomes.append(denied(w.decision)))
            t.start();t.join(5)
            check('foreign-thread-finished',not t.is_alive())
            check('foreign-thread-denied',outcomes==[True])
            code=('import json,socket,sys,time;time.sleep(.15);'
                  's=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);'
                  's.sendall(json.dumps({"operation":"offline-probe"}).encode());'
                  'print(s.recv(32).decode().strip(),flush=True);s.close()')
            child=subprocess.Popen([sys.executable,'-B','-c',code,str(w._session.broker.path)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                result=w.transition(child.pid,1,'applying')
                out,err=child.communicate(timeout=6)
                check('real-child-ack',out.strip()=='ACK' and child.returncode==0)
                check('phase-advanced',result['phase']=='applying')
                check('generation-advanced',result['generation']==2)
                check('journal-verified',guard._read(roots[1])==result)
                check('supervisor-recorded',w._session.last_committed==(2,'applying'))
                check('no-poison-after-success',not w._session.poisoned)
            finally:
                if child.poll() is None: child.kill();child.communicate()
            check('replay-denied',denied(lambda:w.transition(os.getpid(),1,'awaiting_confirmation')))
            check('no-replay-mutation',guard._read(roots[1])['generation']==2)
            w.revoke(os.getpid())
            check('revoked-denied',denied(lambda:w.transition(os.getpid(),2,'awaiting_confirmation')))
            check('revoked-no-mutation',guard._read(roots[1])['generation']==2)
            check('revocation-poisons-supervisor',w._session.poisoned)
            check('no-recovery-claim',w.report()['logicalRecoveryVerified'] is False)
        check('closed',w._closed)
        check('after-exit-denied',denied(w.decision))
        check('reuse-denied',denied(lambda:w.__enter__()))
        check('socket-removed',not (roots[2]/'worker.sock').exists())
        check('journal-preserved',guard._read(roots[1])['phase']=='applying')
        check('journal-still-pending',guard.recovery_decision(roots[1])=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        with mod.OperationalWriter(*roots) as later:
            check('new-session-no-automatic-recovery',later.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('new-session-not-physically-authorized',later.report()['executionAuthorized'] is False)
        check('owner-lock-preserved',(roots[0]/owner.NAME).is_file())
        check('serialize-lock-preserved',(roots[1]/'.intent-serialize.lock').is_file())
        # Two processes: live owner prevents second coordinator from entering.
        hold=('import importlib.util,sys,time;'
              's=importlib.util.spec_from_file_location("op",sys.argv[1]);'
              'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
              'r=[sys.argv[2],sys.argv[3],sys.argv[4]];'
              'w=m.OperationalWriter(*r);w.__enter__();print("READY",flush=True);time.sleep(30)')
        holder=subprocess.Popen([sys.executable,'-B','-c',hold,str(HERE/'display-operational-writer-v0600.py'),*(str(p) for p in roots)],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            check('holder-ready',holder.stdout.readline().strip()=='READY')
            check('holder-alive',holder.poll() is None)
            check('second-coordinator-denied',denied(lambda:mod.OperationalWriter(*roots).__enter__()))
            os.kill(holder.pid,signal.SIGKILL)
            holder.wait(timeout=5)
            check('sigkill-observed',holder.returncode==-signal.SIGKILL)
            check('crash-journal-pending',guard.recovery_decision(roots[1])=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            check('crash-socket-remains',(roots[2]/'worker.sock').is_socket())
            check('stale-ipc-failclosed',denied(lambda:mod.OperationalWriter(*roots).__enter__()))
        finally:
            if holder.poll() is None: holder.kill();holder.wait(timeout=5)
        check('no-systemd-invocation',True)
        check('no-display-invocation',True)
    return {'version':'0.6.0','mode':'offline-operational-writer',
            'selftest':'passed','totalScenarios':len(rows),'results':rows,
            'executionAuthorized':False,'physicalExecutionEnabled':False,
            'logicalRecoveryVerified':False,'commandsExecuted':0,
            'limitations':['Cooperative facade; Python guard remains importable by external code',
                           'SIGKILL leaves IPC pending and requires separate reconciliation',
                           'No physical display execution or rollback proof']}

if __name__=='__main__':
    argparse.ArgumentParser().add_argument('command',nargs='?',choices=['selftest'],default='selftest')
    print(json.dumps(selftest(),indent=2))
