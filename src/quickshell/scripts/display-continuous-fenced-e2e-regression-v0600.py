#!/usr/bin/env python3
"""Offline real-worker end-to-end fenced journal checks. No physical execution."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent

def load(name,key):
    p=HERE/name
    if p.is_symlink() or not p.is_file(): raise RuntimeError('unsafe dependency')
    spec=importlib.util.spec_from_file_location(key,p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

mod=load('display-continuous-fenced-authority-v0600.py','e2e_fenced')
fence=mod.fence
CHILD='''import importlib.util,sys\np=sys.argv[1];s=importlib.util.spec_from_file_location("worker_fixture",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nfor line in sys.stdin:\n if line.strip()=="quit":break\n try: print("ACK" if m.request(sys.argv[2],"offline-probe") else "DENY",flush=True)\n except Exception as e:print("ERROR:"+type(e).__name__,flush=True)\n'''
rows=[]
def check(label,ok):
    rows.append({'scenario':label,'passed':bool(ok)})
    if not ok: raise AssertionError(label)

def worker(sock):
    return subprocess.Popen([sys.executable,'-B','-c',CHILD,str(HERE/'display-worker-authority-v0600.py'),str(sock)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,start_new_session=True,env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','SERPANTINUM_RUNTIME_ADMISSION_FD')})

def stop(p):
    if p.poll() is None:
        try:p.stdin.write('quit\n');p.stdin.flush();p.wait(timeout=2)
        except (BrokenPipeError,subprocess.TimeoutExpired):p.kill();p.wait(timeout=3)
    for f in (p.stdin,p.stdout,p.stderr):f.close()

def main():
    with tempfile.TemporaryDirectory(prefix='serpantinum-fenced-e2e-') as tmp:
        roots=[Path(tmp)/name for name in ('runtime','journal','broker')]
        for p in roots:p.mkdir(mode=0o700)
        check('three-private-roots',all(p.stat().st_mode&0o777==0o700 for p in roots))
        with mod.FencedContinuousSession(*roots) as session:
            check('session-active',session.active)
            check('fencing-report-disabled',session.report()['physicalExecutionEnabled'] is False)
            check('no-initial-journal',fence.read(roots[1]) is None)
            first=session.begin('offline-probe','digest-test')
            check('schema-two',first['schema']==2)
            check('first-generation',first['generation']==1)
            check('recovery-required',first['recovery_required'] is True)
            check('session-id-length',len(first['session_id'])==64)
            check('owner-identity',first['coordinator_identity']==fence._identity())
            p=worker(session.broker.path)
            try:
                check('worker-alive',p.poll() is None)
                p.stdin.write('request\n');p.stdin.flush()
                time.sleep(0.04)
                second=session.transition(p.pid,1,'applying')
                check('real-worker-ack',p.stdout.readline().strip()=='ACK')
                check('transition-applying',second['phase']=='applying')
                check('generation-two',second['generation']==2)
                check('session-preserved',second['session_id']==first['session_id'])
                check('persistent-equals-return',fence.read(roots[1])==second)
                check('last-committed',session.last_committed==(2,'applying'))
                check('still-not-poisoned',session.poisoned is False)
                try:session.transition(p.pid,1,'awaiting_confirmation')
                except mod.FencedAuthorityDenied:pass
                else:raise AssertionError('stale generation accepted')
                check('stale-rejected-no-mutation',fence.read(roots[1])==second)
                session.revoke(p.pid)
                check('revocation-recorded',p.pid in session.revoked)
                try:session.transition(p.pid,2,'awaiting_confirmation')
                except mod.FencedAuthorityDenied:pass
                else:raise AssertionError('revoked worker accepted')
                check('revoked-no-mutation',fence.read(roots[1])==second)
                check('blocked-decision',session.decision()=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
            finally:stop(p)
        check('journal-survives-close',fence.read(roots[1])==second)
        check('broker-socket-cleaned',not (roots[2]/'worker.sock').exists())
        with mod.FencedContinuousSession(*roots) as new_session:
            check('new-session-active',new_session.active)
            try:new_session.begin('offline-probe','another')
            except fence.FenceDenied:pass
            else:raise AssertionError('existing journal adopted')
            check('old-journal-not-adopted',fence.read(roots[1])==second)
            check('new-session-no-physical',new_session.report()['commandsExecuted']==0)
    return {'version':'0.6.0','mode':'offline-continuous-fenced-real-worker','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'limitations':['Cooperative real-worker handshake; not same-UID isolation','No SIGKILL or physical rollback in this suite','No automatic journal recovery']}
if __name__=='__main__':
    try:print(json.dumps(main(),indent=2))
    except BaseException as exc:
        print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),'results':rows,'commandsExecuted':0}));sys.exit(2)
