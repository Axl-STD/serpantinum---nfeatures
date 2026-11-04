#!/usr/bin/env python3
"""Offline characterization of pre-held inherited flock; not worker authentication."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('admission',HERE/'display_runtime_admission_v0592.py')
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

def selftest():
    rows=[]
    def check(name, value):
        rows.append({'scenario':name,'passed':bool(value)})
        if not value: raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-delegation-v0600-') as td:
        root=Path(td)
        os.chmod(root,0o700)
        oldroot=os.environ.get('XDG_RUNTIME_DIR')
        oldfd=os.environ.pop(a.ENV,None)
        os.environ['XDG_RUNTIME_DIR']=str(root)
        try:
            lock=root/a.NAME
            with a.admission(root) as fd:
                check('owner-acquired',fd>=3)
                check('lock-exists',lock.is_file())
                check('owner-fd-exported',a.child_fds()==(fd,))
                with a.admission(root) as same:
                    check('same-process-inherited',same==fd)
                script='import sys;sys.path.insert(0,sys.argv[1]);import display_runtime_admission_v0592 as a;\nwith a.admission(): print("OK")'
                script=script.replace('\\n','\n')
                child=subprocess.run([sys.executable,'-B','-c',script,str(HERE)],pass_fds=(fd,),capture_output=True,text=True,timeout=6)
                check('locked-delegate-accepted',child.returncode==0)
                check('delegate-output',child.stdout.strip()=='OK')
                forged=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
                try:
                    os.environ[a.ENV]=str(forged)
                    try:
                        with a.admission(root): pass
                    except a.AdmissionError:
                        check('unlocked-fd-under-contention-denied',True)
                    else: check('unlocked-fd-under-contention-denied',False)
                finally:
                    os.close(forged)
                    os.environ[a.ENV]=str(fd)
            check('lock-remains',lock.is_file())
            forged=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
            try:
                os.environ[a.ENV]=str(forged)
                try:
                    with a.admission(root): pass
                except a.AdmissionError:
                    check('unlocked-fd-without-contention-denied',True)
                else: check('unlocked-fd-without-contention-denied',False)
            finally:
                os.close(forged)
                os.environ.pop(a.ENV,None)
            with a.admission(root):
                check('owner-reacquires',True)
            check('environment-cleared',a.ENV not in os.environ)
        finally:
            if oldroot is None: os.environ.pop('XDG_RUNTIME_DIR',None)
            else: os.environ['XDG_RUNTIME_DIR']=oldroot
            if oldfd is None: os.environ.pop(a.ENV,None)
            else: os.environ[a.ENV]=oldfd
    return {'version':'0.6.0','mode':'offline-locked-fd-characterization',
            'selftest':'passed','totalScenarios':len(rows),'results':rows,
            'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,
            'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,
            'limitations':['Pre-held flock is verified, not identity or authenticated delegation',
                           'No physical operations or systemd runtime tested']}
if __name__=='__main__':
    if len(sys.argv)>2 or (len(sys.argv)==2 and sys.argv[1]!='selftest'):
        raise SystemExit(2)
    print(json.dumps(selftest(),indent=2))
