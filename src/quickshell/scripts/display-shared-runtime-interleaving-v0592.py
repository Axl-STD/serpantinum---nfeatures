#!/usr/bin/env python3
"""v0.5.92: non-destructive characterization of cross-entrypoint lock interleaving.

This does NOT install universal exclusion. It tests the current independent
lock domains using private disposable directories and processes only.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

VERSION = '0.5.92'
CHILD = '''import fcntl,os,sys,time
p=sys.argv[1]
f=os.open(p,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
try:
 fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 print('ACQUIRED',flush=True)
 time.sleep(0.02)
except BlockingIOError:
 print('BUSY',flush=True)
finally:
 os.close(f)
'''

def probe(path):
    p = subprocess.run([sys.executable, '-c', CHILD, str(path)],
                       text=True, capture_output=True, timeout=15, check=True)
    return p.stdout.strip()

def run():
    cases=[]
    def check(name, value):
        if not value:
            raise AssertionError(name)
        cases.append({'scenario': name, 'passed': True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0592-lock-') as directory:
        root=Path(directory)
        os.chmod(root,0o700)
        check('private-root', stat.S_IMODE(root.stat().st_mode)==0o700)
        a=root/'legacy.lock'
        b=root/'global.lock'
        check('distinct-lock-paths', a != b)
        fd=os.open(a,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            check('legacy-lock-acquired', True)
            check('legacy-contention-rejected', probe(a)=='BUSY')
            check('global-admission-independent', probe(b)=='ACQUIRED')
            check('legacy-file-private', stat.S_IMODE(a.stat().st_mode)==0o600)
            check('global-file-private', stat.S_IMODE(b.stat().st_mode)==0o600)
        finally:
            os.close(fd)
        check('legacy-lock-recovered', probe(a)=='ACQUIRED')
        fd=os.open(b,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            check('global-lock-acquired', True)
            check('global-contention-rejected', probe(b)=='BUSY')
            check('legacy-independent-of-global', probe(a)=='ACQUIRED')
        finally:
            os.close(fd)
        check('global-lock-recovered', probe(b)=='ACQUIRED')
        check('lock-files-preserved', a.is_file() and b.is_file())
    return {'version':VERSION,'mode':'offline-cross-entrypoint-lock-characterization',
            'selftest':'passed','totalScenarios':len(cases),'results':cases,
            'universalRuntimeExclusionVerified':False,
            'authenticatedDelegationImplemented':False,
            'productionModified':False,'systemdIntegration':False,
            'automaticCleanup':False,'resourceOwnershipVerified':False,
            'executionAuthorized':False,'physicalExecutionEnabled':False,
            'physicalRollbackVerified':False,'commandsExecuted':0,
            'limitations':['Disposable locks demonstrate the gap, not a production race',
                           'No universal lock installed or delegation authenticated',
                           'No real systemd units or display operations']}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['selftest'])
    parser.parse_args()
    try:
        result=run()
    except Exception as exc:
        print(json.dumps({'version':VERSION,'selftest':'failed','error':str(exc),
                          'commandsExecuted':0}))
        return 1
    print(json.dumps(result,indent=2))
    return 0

if __name__=='__main__':
    sys.exit(main())
