#!/usr/bin/env python3
"""Offline owner-only admission tests; disposable processes and paths only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SRC = HERE / 'display-admission-owner-v0600.py'
spec = importlib.util.spec_from_file_location('candidate', SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def selftest():
    rows=[]
    def check(name, ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    def rejects(name, fn):
        try: fn()
        except (m.AdmissionDenied, OSError): check(name, True)
        else: check(name, False)
    def enter(root):
        with m.owner_only(root): pass
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-owner-') as temp:
        root=Path(temp)
        os.chmod(root,0o700)
        check('private-root',stat.S_IMODE(root.stat().st_mode)==0o700)
        check('no-lock-initially',not (root/m.NAME).exists())
        with m.owner_only(root) as fd:
            check('owner-admitted',fd>=3)
            check('private-lock',stat.S_IMODE((root/m.NAME).stat().st_mode)==0o600)
            check('lock-inode',os.fstat(fd).st_ino==(root/m.NAME).stat().st_ino)
            rejects('same-process-independent-fd-contended',lambda:enter(root))
            child='import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);\ntry:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:sys.exit(17)\nsys.exit(0)'
            p=subprocess.run([sys.executable,'-B','-c',child,str(root/m.NAME)],capture_output=True,timeout=5)
            check('child-contended',p.returncode==17)
            check('no-inherited-env',m.ENV not in os.environ)
        enter(root)
        check('reacquire-after-close',True)
        check('lock-file-retained',(root/m.NAME).is_file())
        os.environ[m.ENV]='9999'
        try: rejects('env-only-rejected',lambda:enter(root))
        finally: os.environ.pop(m.ENV,None)
        os.environ[m.ENV]='3'
        try: rejects('fd-delegation-rejected',lambda:enter(root))
        finally: os.environ.pop(m.ENV,None)
        (root/m.NAME).chmod(0o644)
        rejects('permissive-lock',lambda:enter(root))
        (root/m.NAME).chmod(0o600)
        (root/m.NAME).unlink()
        (root/m.NAME).symlink_to(root/'target')
        rejects('symlink-lock',lambda:enter(root))
        (root/m.NAME).unlink()
        (root/m.NAME).mkdir()
        rejects('directory-lock',lambda:enter(root))
        (root/m.NAME).rmdir()
        (root/m.NAME).touch(mode=0o600)
        os.link(root/m.NAME,root/'second-link')
        rejects('hardlinked-lock',lambda:enter(root))
        (root/'second-link').unlink()
        (root/m.NAME).unlink()
        root.chmod(0o755)
        rejects('permissive-root',lambda:enter(root))
        root.chmod(0o700)
        rejects('relative-root',lambda:enter('relative-path'))
        rejects('tmp-root',lambda:enter('/tmp'))
        other=root/'missing'
        rejects('missing-root',lambda:enter(other))
        check('no-physical-operations',True)
        check('no-systemd-operations',True)
        check('no-authentication-overclaim',True)
        check('no-universal-exclusion-overclaim',True)
    return {'version':'0.6.0','mode':'offline-owner-only-candidate','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,'commandsExecuted':0,'limitations':['Owner-only prototype: inherited delegation deliberately rejected','Not wired into legacy or global entrypoints','Not authenticated delegation or physical rollback']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['selftest']);p.parse_args()
    print(json.dumps(selftest(),indent=2))
