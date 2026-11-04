#!/usr/bin/env python3
"""Offline SIGKILL/SIGTERM fault injection for v0.6.0; disposable child only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent

def load():
    p=HERE/'display-transaction-guard-v0600.py'
    s=importlib.util.spec_from_file_location('display_guard_fault_child',p)
    m=importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

def child(root,marker,stage,operation):
    g=load()
    orig_replace=os.replace
    orig_fsync=os.fsync
    orig_mkstemp=tempfile.mkstemp
    def pause():
        marker.write_text('READY')
        while True:
            time.sleep(1)
    def mkstemp(*a,**kw):
        result=orig_mkstemp(*a,**kw)
        if stage=='after_temp_create': pause()
        return result
    def fsync(fd):
        if stage=='before_file_fsync' and stat.S_ISREG(os.fstat(fd).st_mode): pause()
        if stage=='before_directory_fsync' and stat.S_ISDIR(os.fstat(fd).st_mode): pause()
        return orig_fsync(fd)
    def replace(*a,**kw):
        if stage=='before_replace': pause()
        r=orig_replace(*a,**kw)
        if stage=='after_replace': pause()
        return r
    tempfile.mkstemp=mkstemp
    os.fsync=fsync
    os.replace=replace
    if operation=='prepare': g.prepare(root,'test-operation','snapshot-placeholder')
    else: g.advance(root,1,'applying')
    raise RuntimeError('fixture failed to pause')

def selftest():
    g=load()
    rows=[]
    def check(name,ok):
        rows.append({'scenario':name,'passed':bool(ok)})
        if not ok: raise AssertionError(name)
    stages=('after_temp_create','before_file_fsync','before_replace','after_replace','before_directory_fsync')
    for operation in ('prepare','advance'):
        for stage in stages:
            for sig in (signal.SIGTERM,signal.SIGKILL):
                with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-kill-') as tmp:
                    root=Path(tmp)/'journal';root.mkdir(mode=0o700)
                    marker=Path(tmp)/'ready'
                    if operation=='advance': g.prepare(root,'test-operation','snapshot-placeholder')
                    prior=g._read(root)
                    proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'child',str(root),str(marker),stage,operation],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                    try:
                        deadline=time.monotonic()+5
                        while not marker.exists() and proc.poll() is None and time.monotonic()<deadline: time.sleep(.01)
                        check(operation+'-'+stage+'-'+sig.name+'-ready',marker.exists() and proc.poll() is None)
                        proc.send_signal(sig)
                        proc.communicate(timeout=4)
                        check(operation+'-'+stage+'-'+sig.name+'-terminated',proc.returncode==-sig)
                        current=g._read(root)
                        expected_new=stage in ('after_replace','before_directory_fsync')
                        check(operation+'-'+stage+'-'+sig.name+'-atomic-state',current is not None and current['generation']==(2 if operation=='advance' and expected_new else 1) and current['phase']==('applying' if operation=='advance' and expected_new else 'prepared') if operation=='advance' else (current is None if not expected_new else current is not None and current['phase']=='prepared'))
                        decision=g.recovery_decision(root)
                        check(operation+'-'+stage+'-'+sig.name+'-conservative',decision==('NO_JOURNAL_NOT_PROOF_OF_SAFETY' if current is None else 'BLOCK_AND_RECONCILE_EXTERNAL_STATE'))
                        temps=list(root.glob('.intent-*'))
                        check(operation+'-'+stage+'-'+sig.name+'-temp-scope',all(p.is_file() and not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)==0o600 for p in temps))
                        check(operation+'-'+stage+'-'+sig.name+'-no-auto-clean',bool(temps)==(not expected_new))
                        if current is not None:
                            check(operation+'-'+stage+'-'+sig.name+'-identity',current['operation']=='test-operation' and current['snapshot_digest']=='snapshot-placeholder')
                        else:
                            check(operation+'-'+stage+'-'+sig.name+'-missing-not-safe',decision!='TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION')
                    finally:
                        if proc.poll() is None:
                            proc.kill();proc.communicate(timeout=4)
    return {'version':'0.6.0','mode':'offline-process-kill-write-windows','selftest':'passed','totalScenarios':len(rows),'results':rows,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'systemdIntegration':False,'limitations':['Process kill is not power-loss durability proof','Orphaned temporary files require conservative cleanup protocol','No physical display rollback or authenticated delegation']}

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('selftest','child'));p.add_argument('args',nargs='*');a=p.parse_args()
    if a.command=='child':
        if len(a.args)!=4: return 2
        child(Path(a.args[0]),Path(a.args[1]),a.args[2],a.args[3]);return 3
    print(json.dumps(selftest(),indent=2));return 0
if __name__=='__main__': sys.exit(main())
