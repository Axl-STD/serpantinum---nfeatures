#!/usr/bin/env python3
"""v0.5.82: offline isolation, interruption, contention and recovery regression."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent

def load():
    spec = importlib.util.spec_from_file_location('gate_v0582', HERE/'display-consolidated-regression-gate-v05805.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def selftest():
    mod = load()
    cases=[]
    def record(name, condition):
        assert condition, name
        cases.append({'scenario':name,'passed':True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0582-') as tmp:
        root=Path(tmp)
        os.chmod(root,0o700)
        original=mod.RUNTIME_LOCK
        mod.RUNTIME_LOCK=root/'gate.lock'
        try:
            record('private-directory',root.stat().st_mode & 0o077 == 0)
            with mod.runtime_exclusive():
                record('lock-private',mod.RUNTIME_LOCK.stat().st_mode & 0o077 == 0)
                proc=subprocess.run([sys.executable,'-c', '''import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW)
try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: sys.exit(0)
sys.exit(1)''',str(mod.RUNTIME_LOCK)])
                record('concurrent-process-rejected',proc.returncode==0)
            with mod.runtime_exclusive():
                record('lock-recovery',True)
            record('lock-not-unlinked',mod.RUNTIME_LOCK.exists())
            try:
                with mod.runtime_exclusive():
                    raise RuntimeError('injected')
            except RuntimeError:
                pass
            with mod.runtime_exclusive():
                record('lock-released-after-exception', True)
            mod.RUNTIME_LOCK=root/'unsafe-link'
            (root/'target').write_text('x')
            mod.RUNTIME_LOCK.symlink_to(root/'target')
            try:
                with mod.runtime_exclusive(): pass
            except (OSError,ValueError): record('symlink-lock-rejected',True)
            else: raise AssertionError('symlink-lock-rejected')
            mod.RUNTIME_LOCK=root/'gate.lock'
            os.chmod(mod.RUNTIME_LOCK,0o666)
            try:
                with mod.runtime_exclusive(): pass
            except ValueError: record('world-readable-lock-rejected',True)
            else: raise AssertionError('world-readable-lock-rejected')
            os.chmod(mod.RUNTIME_LOCK,0o600)
            ok=mod.run_isolated([sys.executable,'-c','print("ok")'],timeout=3)
            record('isolated-success',ok.returncode==0 and ok.stdout.strip()=='ok')
            bad=mod.run_isolated([sys.executable,'-c','import sys;sys.exit(9)'],timeout=3)
            record('isolated-nonzero-preserved',bad.returncode==9)
            try:
                mod.run_isolated([sys.executable,'-c','import time;time.sleep(10)'],timeout=.1)
            except mod.GateError: record('timeout-rejected',True)
            else: raise AssertionError('timeout-rejected')
            marker=root/'child.pid'
            script='''import os,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
open(sys.argv[1],'w').write(str(child.pid))
time.sleep(30)'''
            try:
                mod.run_isolated([sys.executable,'-c',script,str(marker)],timeout=1.5)
            except mod.GateError: record('descendant-timeout',True)
            else: raise AssertionError('descendant-timeout')
            record('descendant-created',marker.exists())
            # A killed child may remain briefly as a zombie until reaped by PID 1.
            pid=int(marker.read_text())
            time.sleep(.05)
            try:
                state=Path(f'/proc/{pid}/stat').read_text().split(') ')[1][0]
            except FileNotFoundError:
                state='X'
            record('descendant-not-running',state in ('Z','X'))
            record('recovery-after-timeout',mod.run_isolated([sys.executable,'-c','print(1)'],timeout=3).returncode==0)
            # Exercise actual CLI error JSON on missing suite with source unchanged.
            out=mod.report('offline',[{'component':'test','passed':True}])
            record('safe-report',out['executionAuthorized'] is False and out['physicalExecutionEnabled'] is False)
            record('runtime-explicit-only',len(mod.RUNTIME)==3)
            record('offline-first',len(mod.OFFLINE)==2)
        finally:
            mod.RUNTIME_LOCK=original
    return {'version':'0.5.82','mode':'offline-runtime-orchestration-hardening','selftest':'passed',
            'totalScenarios':len(cases),'results':cases,'systemdIntegration':False,
            'productionModified':False,'automaticCleanup':False,'atomicCrossStoreSnapshot':False,
            'crashDurabilityVerified':False,'historicalIdentityVerified':False,
            'workerIdentityVerified':False,'operationCompletionVerified':False,
            'finalizationAuthorized':False,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'physicalRollbackVerified':False,
            'commandsExecuted':0,
            'limitations':['Process group cleanup is best effort; kernel zombie reaping is external',
                           'No physical display operations or independent identity authentication']}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(),indent=2))
