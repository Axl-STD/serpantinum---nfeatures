#!/usr/bin/env python3
"""Offline IPC reconciliation regression: disposable sockets/processes only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ipc', HERE/'display-broker-ipc-reconcile-v0600.py')
ipc=importlib.util.module_from_spec(spec); spec.loader.exec_module(ipc)

CHILD="import importlib.util,sys,time\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location('session',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\nwith m.Session(*map(Path,sys.argv[2:5])):\n    m.serialized.guard.prepare(Path(sys.argv[3]),'fixture','digest')\n    if sys.argv[5]=='applying': m.serialized.guard.advance(Path(sys.argv[3]),1,'applying')\n    print('READY',flush=True)\n    time.sleep(20)\n"

def selftest():
    rows=[]
    def check(name,ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    for signum in (signal.SIGTERM,signal.SIGKILL):
        for phase in ('prepared','applying'):
            with tempfile.TemporaryDirectory(prefix='serpantinum-ipc-reconcile-') as tmp:
                base=Path(tmp); roots=[base/n for n in ('runtime','journal','broker')]
                for r in roots:r.mkdir(mode=0o700)
                runtime,journal,broker=roots
                prefix=signal.Signals(signum).name+'-'+phase
                proc=subprocess.Popen([sys.executable,'-B','-c',CHILD,str(HERE/'display-authority-session-v0600.py'),*(str(x) for x in roots),phase],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                try:
                    import select
                    ready,_,_=select.select([proc.stdout],[],[],5)
                    marker=proc.stdout.readline().strip() if ready else ''
                    check(prefix+'-ready',marker=='READY')
                    check(prefix+'-socket', (broker/'worker.sock').is_socket())
                    active=ipc.reconcile(*roots)
                    check(prefix+'-active-blocked',active['decision']=='BLOCK_AND_INVESTIGATE')
                    check(prefix+'-active-retained',(broker/'worker.sock').is_socket())
                    os.kill(proc.pid,signum);proc.wait(timeout=5)
                    check(prefix+'-terminated',proc.returncode==-signum)
                    recovered=ipc.reconcile(*roots)
                    check(prefix+'-ipc-reclaimed',recovered['socketRemoved'] is True)
                    check(prefix+'-ipc-only',recovered['decision']=='IPC_RECLAIMED_LOGICAL_STATE_UNVERIFIED')
                    check(prefix+'-journal-blocks',recovered['journalDecision']=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
                    check(prefix+'-journal-preserved',(journal/'intent.json').is_file())
                    check(prefix+'-socket-gone',not os.path.lexists(broker/'worker.sock'))
                    repeat=ipc.reconcile(*roots)
                    check(prefix+'-idempotent',repeat['decision']=='IPC_ABSENT_LOGICAL_STATE_UNVERIFIED')
                    check(prefix+'-no-physical',not recovered['executionAuthorized'] and not recovered['physicalExecutionEnabled'] and recovered['commandsExecuted']==0)
                    check(prefix+'-no-logical-proof',recovered['logicalRecoveryVerified'] is False)
                    with ipc.owner.owner_only(runtime):
                        busy=ipc.reconcile(*roots)
                        check(prefix+'-contention-blocks',busy['decision']=='BLOCK_AND_INVESTIGATE')
                finally:
                    if proc.poll() is None:proc.kill();proc.wait(timeout=5)
                    proc.stdout.close();proc.stderr.close()
    with tempfile.TemporaryDirectory(prefix='serpantinum-ipc-adversarial-') as tmp:
        base=Path(tmp); roots=[base/n for n in ('runtime','journal','broker')]
        for r in roots:r.mkdir(mode=0o700)
        runtime,journal,broker=roots
        path=broker/'worker.sock'
        result=ipc.reconcile(*roots)
        check('absent-not-proof',result['logicalRecoveryVerified'] is False)
        path.symlink_to(base/'other')
        check('symlink-rejected',ipc.reconcile(*roots)['decision']=='BLOCK_AND_INVESTIGATE')
        check('symlink-preserved',path.is_symlink())
        path.unlink();path.write_text('not socket')
        check('regular-rejected',ipc.reconcile(*roots)['decision']=='BLOCK_AND_INVESTIGATE')
        check('regular-preserved',path.is_file())
        path.unlink()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.bind(str(path));s.listen(1);os.chmod(path,0o600)
            check('live-rejected',ipc.reconcile(*roots)['decision']=='BLOCK_AND_INVESTIGATE')
            check('live-preserved',path.is_socket())
        path.unlink()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.bind(str(path));os.chmod(path,0o644)
        check('permissive-rejected',ipc.reconcile(*roots)['decision']=='BLOCK_AND_INVESTIGATE')
        path.unlink()
        broker.chmod(0o755)
        check('unsafe-broker-rejected',ipc.reconcile(*roots)['decision']=='BLOCK_AND_INVESTIGATE')
        broker.chmod(0o700)
        check('no-auto-cleanup',ipc.reconcile(*roots)['cleanupAuthorized'] is False)
    return {'version':'0.6.0','mode':'offline-ipc-reconcile-regression','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'cleanupAuthorized':False,'commandsExecuted':0,
            'limitations':['IPC-only reclamation; logical journal recovery not authorized',
                           'Same-UID hostile races and surviving workers not excluded',
                           'No physical monitor execution or rollback']}
if __name__=='__main__':
    argparse.ArgumentParser().add_argument('command',choices=['selftest'])
    if len(sys.argv)!=2 or sys.argv[1]!='selftest':sys.exit(2)
    try:print(json.dumps(selftest(),indent=2))
    except BaseException as e:
        print(json.dumps({'selftest':'failed','error':str(e),'errorType':type(e).__name__}))
        sys.exit(2)
