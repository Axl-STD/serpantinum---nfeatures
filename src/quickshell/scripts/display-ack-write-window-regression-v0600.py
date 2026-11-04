#!/usr/bin/env python3
"""Offline ACK-to-write fault injection against actual worker handshakes. No display calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ack_fenced',HERE/'display-continuous-fenced-authority-v0600.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
fence=mod.fence
CHILD='''import importlib.util,sys
s=importlib.util.spec_from_file_location("w",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
for line in sys.stdin:
 if line.strip()=="quit":break
 try:print("ACK" if m.request(sys.argv[2],"offline-probe") else "DENY",flush=True)
 except Exception as e:print("ERROR:"+type(e).__name__,flush=True)
'''
rows=[]
def check(label,condition):
    ok=bool(condition);rows.append({'scenario':label,'passed':ok})
    if not ok:raise AssertionError(label)
def worker(path):
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','SERPANTINUM_RUNTIME_ADMISSION_FD')}
    env['PYTHONDONTWRITEBYTECODE']='1'
    return subprocess.Popen([sys.executable,'-B','-c',CHILD,str(HERE/'display-worker-authority-v0600.py'),str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,start_new_session=True,env=env)
def stop(p):
    if p.poll() is None:
        try:p.stdin.write('quit\n');p.stdin.flush();p.wait(timeout=2)
        except (BrokenPipeError,subprocess.TimeoutExpired):p.kill();p.wait(timeout=3)
    for stream in (p.stdin,p.stdout,p.stderr):stream.close()
def scenario(kind):
    with tempfile.TemporaryDirectory(prefix='serpantinum-ack-write-') as tmp:
        roots=[Path(tmp)/n for n in ('runtime','journal','broker')]
        for r in roots:r.mkdir(mode=0o700)
        with mod.FencedContinuousSession(*roots) as session:
            initial=session.begin('offline-probe','digest-ack')
            check(kind+'-initial-generation',initial['generation']==1)
            check(kind+'-recovery-required',initial['recovery_required'] is True)
            p=worker(session.broker.path)
            try:
                check(kind+'-worker-running',p.poll() is None)
                orig_serve=session.broker.serve_once
                orig_advance=fence.advance
                orig_live=session._live
                observed={'ack':False,'write':False}
                def serve_hook():
                    result=orig_serve();observed['ack']=result['accepted']
                    if kind=='revoke-after-ack':session.revoke(p.pid)
                    elif kind=='journal-change-after-ack':
                        orig_advance(roots[1],initial['session_id'],1,'applying')
                    elif kind=='socket-replaced-after-ack':
                        os.unlink(session.broker.path)
                    return result
                def advance_hook(*args,**kwargs):
                    observed['write']=True
                    if kind=='reject-at-write':raise RuntimeError('injected before write')
                    result=orig_advance(*args,**kwargs)
                    if kind=='raise-after-write':raise RuntimeError('injected after write')
                    return result
                session.broker.serve_once=serve_hook
                fence.advance=advance_hook
                try:
                    p.stdin.write('request\n');p.stdin.flush();time.sleep(.04)
                    try:session.transition(p.pid,1,'applying')
                    except Exception as exc:
                        check(kind+'-rejected',isinstance(exc,(RuntimeError,OSError)))
                    else:raise AssertionError(kind+'-unexpected-accept')
                finally:
                    session.broker.serve_once=orig_serve
                    fence.advance=orig_advance
                    session._live=orig_live
                check(kind+'-ack-observed',observed['ack'])
                check(kind+'-worker-ack',p.stdout.readline().strip()=='ACK')
                check(kind+'-poisoned',session.poisoned)
                after=fence.read(roots[1]);check(kind+'-journal-valid',after is not None and after['recovery_required'])
                if kind in ('revoke-after-ack','socket-replaced-after-ack','reject-at-write'):
                    check(kind+'-no-write',after==initial)
                else:
                    check(kind+'-generation-two',after['generation']==2 and after['phase']=='applying')
                check(kind+'-write-hook',observed['write']==(kind in ('reject-at-write','raise-after-write')))
                try:session.transition(p.pid,after['generation'],'awaiting_confirmation')
                except Exception:check(kind+'-poison-blocks-retry',True)
                else:raise AssertionError(kind+'-poison-bypass')
                check(kind+'-journal-stable-after-retry',fence.read(roots[1])==after)
            finally:stop(p)
        check(kind+'-journal-survives-close',fence.read(roots[1]) is not None)
        with mod.FencedContinuousSession(*roots) as new_session:
            try:new_session.begin('offline-probe','replacement')
            except fence.FenceDenied:check(kind+'-no-adoption',True)
            else:raise AssertionError(kind+'-adoption')
        check(kind+'-no-physical',True)
def main():
    for kind in ('revoke-after-ack','journal-change-after-ack','reject-at-write','raise-after-write'):
        scenario(kind)
    return {'version':'0.6.0','mode':'offline-ack-to-write-fault-injection','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'limitations':['Cooperative hooks, not CPU revocation or power-loss durability','No physical display execution or rollback','Same-UID adversarial filesystem races not excluded']}
if __name__=='__main__':
    try:print(json.dumps(main(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),'results':rows,'commandsExecuted':0}));sys.exit(2)
