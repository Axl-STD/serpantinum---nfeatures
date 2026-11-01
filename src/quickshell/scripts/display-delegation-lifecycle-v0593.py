#!/usr/bin/env python3
"""Offline v0.5.93 lifecycle characterization. Signals only directly spawned fixtures."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import display_runtime_admission_v0592 as adm

CHILD = r'''
import fcntl,os,sys,time
from pathlib import Path
root=Path(sys.argv[1]); role=sys.argv[2]; fd=int(sys.argv[3]); marker=root/(role+'.ready')
if role=='keeper':
    marker.touch()
    while not (root/'release').exists(): time.sleep(.02)
elif role=='close-keeper':
    os.close(fd)
    marker.touch()
    while not (root/'release').exists(): time.sleep(.02)
elif role=='dup-keeper':
    d=os.dup(fd);os.close(fd)
    marker.touch()
    while not (root/'release').exists(): time.sleep(.02)
    os.close(d)
elif role=='delegate':
    sys.path.insert(0,sys.argv[4])
    import display_runtime_admission_v0592 as a
    with a.admission(root):
        marker.touch()
        time.sleep(.12)
else:
    sys.exit(3)
'''

def wait_marker(path, proc, timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.exists(): return True
        if proc.poll() is not None: return False
        time.sleep(.015)
    return False

def spawn(root, role, fd, env, inherited=True):
    return subprocess.Popen([sys.executable,'-B','-c',CHILD,str(root),role,str(fd),str(HERE)],
                            env=env,pass_fds=(fd,) if inherited else (),
                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                            start_new_session=True)

def stop(proc):
    if proc.poll() is None:
        proc.kill()
    try: proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill();proc.communicate(timeout=3)

def locked_by_other(path):
    p=subprocess.run([sys.executable,'-B','-c',
        'import os,fcntl,sys;f=os.open(sys.argv[1],os.O_RDWR);\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:sys.exit(7)\nsys.exit(0)',str(path)],
        capture_output=True,timeout=3)
    return p.returncode==7

def run():
    rows=[]
    def check(name, cond, kind='PASS'):
        rows.append({'scenario':name,'passed':bool(cond),'classification':kind if cond else 'FAIL'})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0593-lifecycle-') as td:
        root=Path(td)/'runtime';root.mkdir(mode=0o700)
        old=os.environ.get('XDG_RUNTIME_DIR');prior=os.environ.pop(adm.ENV,None)
        os.environ['XDG_RUNTIME_DIR']=str(root)
        env=os.environ.copy();env.pop(adm.ENV,None)
        try:
            check('private-root',root.stat().st_mode & 0o077==0)
            with adm.admission(root) as fd:
                lock=root/adm.NAME
                check('owner-lock-held',locked_by_other(lock))
                check('lock-file-private',lock.stat().st_mode & 0o077==0)
                check('lock-file-single-link',lock.stat().st_nlink==1)
                check('child-fds-exact',adm.child_fds()==(fd,))
                check('descriptor-valid',os.fstat(fd).st_ino==lock.stat().st_ino)
                p=spawn(root,'delegate',fd,os.environ.copy())
                try:
                    check('delegated-process-accepted',wait_marker(root/'delegate.ready',p))
                    p.wait(timeout=3)
                    check('delegated-process-exited',p.returncode==0)
                    check('parent-lock-survives-delegate-exit',locked_by_other(lock))
                finally:stop(p)
                duplicate=os.dup(fd)
                try:
                    check('duplicate-same-inode',os.fstat(duplicate).st_ino==os.fstat(fd).st_ino)
                    check('duplicate-retains-contention',locked_by_other(lock))
                finally:os.close(duplicate)
                check('closing-duplicate-retains-owner',locked_by_other(lock))
            check('owner-release-unlocks',not locked_by_other(lock))
            check('lock-file-survives-release',lock.is_file())
            with adm.admission(root):check('reacquisition-succeeds',locked_by_other(lock))
            # Keepers inherit a locked open-file-description, then original owner exits.
            for role in ('keeper','dup-keeper','close-keeper'):
                (root/'release').unlink(missing_ok=True)
                (root/(role+'.ready')).unlink(missing_ok=True)
                with adm.admission(root) as fd:
                    p=spawn(root,role,fd,os.environ.copy())
                    try:
                        check(role+'-ready',wait_marker(root/(role+'.ready'),p))
                    finally:
                        # On failure do not leak a keeper.
                        if not (root/(role+'.ready')).exists():stop(p)
                try:
                    expected=role!='close-keeper'
                    check(role+'-post-parent-contention',locked_by_other(lock)==expected,
                          'PASS' if expected else 'GAP')
                    check(role+'-parent-exit-not-logical-proof',True,'NOT_VERIFIED')
                    (root/'release').touch()
                    p.wait(timeout=3)
                    check(role+'-child-exits',p.returncode==0)
                    check(role+'-final-release',not locked_by_other(lock))
                finally:stop(p)
            # Signal-only direct child fixtures, not the session or any systemd service.
            for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGKILL):
                (root/'release').unlink(missing_ok=True)
                (root/'keeper.ready').unlink(missing_ok=True)
                with adm.admission(root) as fd:
                    p=spawn(root,'keeper',fd,os.environ.copy())
                    try: check(sig.name+'-ready',wait_marker(root/'keeper.ready',p))
                    finally:
                        if not (root/'keeper.ready').exists():stop(p)
                try:
                    check(sig.name+'-lock-held-before-signal',locked_by_other(lock))
                    p.send_signal(sig)
                    p.wait(timeout=3)
                    check(sig.name+'-fixture-terminated',p.returncode==-sig)
                    check(sig.name+'-lock-released',not locked_by_other(lock))
                    check(sig.name+'-lock-file-preserved',lock.is_file())
                finally:stop(p)
            # An unlocked matching descriptor can enter delegated branch: documented GAP.
            fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
            try:
                os.environ[adm.ENV]=str(fd)
                with adm.admission(root):
                    check('unlocked-descriptor-acquires-lock',locked_by_other(lock),'GAP')
            finally:
                os.environ.pop(adm.ENV,None);os.close(fd)
            check('no-residual-admission-env',adm.ENV not in os.environ)
            check('no-residual-lock',not locked_by_other(lock))
        finally:
            if old is None:os.environ.pop('XDG_RUNTIME_DIR',None)
            else:os.environ['XDG_RUNTIME_DIR']=old
            if prior is not None:os.environ[adm.ENV]=prior
    ok=all(x['passed'] for x in rows)
    return {'version':'0.5.93','mode':'offline-delegation-lifecycle',
            'selftest':'passed' if ok else 'failed','totalScenarios':len(rows),
            'results':rows,'unlockedDescriptorGapReproduced':True,
            'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,
            'logicalRecoveryVerified':False,'systemdIntegration':False,
            'productionModified':False,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'commandsExecuted':0,
            'limitations':['Kernel lock release is not logical rollback',
                           'Unlocked matching descriptor acquires lock in delegated path',
                           'No authenticated worker identity or universal exclusion',
                           'No real systemd or display operations']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('selftest',));parser.parse_args()
    try:result=run()
    except BaseException as exc:
        result={'version':'0.5.93','selftest':'failed','error':type(exc).__name__+': '+str(exc),
                'physicalExecutionEnabled':False,'commandsExecuted':0}
    print(json.dumps(result,indent=2))
    return 0 if result['selftest']=='passed' else 2

if __name__=='__main__':sys.exit(main())
