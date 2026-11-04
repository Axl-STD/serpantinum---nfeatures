#!/usr/bin/env python3
"""Offline real surviving worker against terminated coordinator; no display operations."""
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
COORD = r'''
import importlib.util,sys,time
from pathlib import Path
here=Path(sys.argv[1]); roots=[Path(x) for x in sys.argv[2:5]]
marker=Path(sys.argv[5]); phase=sys.argv[6]
s=importlib.util.spec_from_file_location('coordinator',here/'display-continuous-fenced-authority-v0600.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with m.FencedContinuousSession(*roots) as session:
 if phase=='prepared':session.begin('offline-probe','digest')
 marker.write_text(str(session.broker.path))
 time.sleep(90)
'''
WORKER = r'''
import importlib.util,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('worker',Path(sys.argv[1]))
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print('READY',flush=True)
for line in sys.stdin:
 if line.strip()=='quit':break
 try: print('ACK' if m.request(sys.argv[2],'offline-probe') else 'DENY',flush=True)
 except Exception as exc: print('ERROR:'+type(exc).__name__,flush=True)
'''
PROBE = r'''
import importlib.util,sys
from pathlib import Path
here=Path(sys.argv[1]);roots=[Path(x) for x in sys.argv[2:5]]
s=importlib.util.spec_from_file_location('fenced',here/'display-continuous-fenced-authority-v0600.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
try:
 with m.FencedContinuousSession(*roots) as session:
  session.begin('offline-probe','new')
except m.fence.FenceDenied:sys.exit(17)
except Exception as exc:
 print(type(exc).__name__+':'+str(exc),file=sys.stderr)
 sys.exit(19)
else:sys.exit(0)
'''

def load():
 p=HERE/'display-session-fencing-journal-v0600.py'
 s=importlib.util.spec_from_file_location('survivor_fence',p)
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def run():
 rows=[]
 def check(name,ok):
  rows.append({'scenario':name,'passed':bool(ok)})
  if not ok:raise AssertionError(name)
 def wait_marker(path,proc):
  until=time.monotonic()+8
  while time.monotonic()<until:
   if path.exists():return True
   if proc.poll() is not None:return False
   time.sleep(.02)
  return False
 def stop(proc):
  if proc.poll() is None:proc.kill()
  try:proc.communicate(timeout=4)
  except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=4)
 fence=load()
 env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','SERPANTINUM_RUNTIME_ADMISSION_FD')}
 env['PYTHONDONTWRITEBYTECODE']='1'
 with tempfile.TemporaryDirectory(prefix='serpantinum-survivor-v0600-') as td:
  for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGKILL):
   for phase in ('empty','prepared'):
    prefix=sig.name+'-'+phase
    root=Path(td)/prefix;root.mkdir(mode=0o700)
    roots=[root/x for x in ('runtime','journal','broker')]
    for r in roots:r.mkdir(mode=0o700)
    marker=root/'ready'
    coordinator=subprocess.Popen([sys.executable,'-B','-c',COORD,str(HERE),*[str(x) for x in roots],str(marker),phase],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    worker=None
    try:
     check(prefix+'-coordinator-ready',wait_marker(marker,coordinator))
     old=fence.read(roots[1])
     check(prefix+'-expected-journal',(old is None) if phase=='empty' else (old is not None and old['generation']==1))
     worker=subprocess.Popen([sys.executable,'-B','-c',WORKER,str(HERE/'display-worker-authority-v0600.py'),str(roots[2]/'worker.sock')],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,start_new_session=True)
     check(prefix+'-worker-ready',worker.stdout.readline().strip()=='READY')
     os.kill(coordinator.pid,sig)
     coordinator.wait(timeout=6)
     check(prefix+'-coordinator-terminated',coordinator.poll() is not None)
     check(prefix+'-worker-survives',worker.poll() is None)
     worker.stdin.write('request\n');worker.stdin.flush()
     response=worker.stdout.readline().strip()
     check(prefix+'-stale-request-not-ack',response!='ACK')
     check(prefix+'-stale-request-error',response.startswith('ERROR:') or response=='DENY')
     check(prefix+'-journal-preserved',fence.read(roots[1])==old)
     probe=subprocess.run([sys.executable,'-B','-c',PROBE,str(HERE),*[str(x) for x in roots]],env=env,capture_output=True,text=True,timeout=15)
     check(prefix+'-replacement-outcome',probe.returncode==(0 if old is None else 17))
     check(prefix+'-post-probe-journal',(fence.read(roots[1]) is not None) if old is None else fence.read(roots[1])==old)
     if old is not None:
      check(prefix+'-recovery-required',old['recovery_required'] is True)
      check(prefix+'-foreign-coordinator',old['coordinator_identity']!=fence._identity())
    finally:
     stop(coordinator)
     if worker is not None:
      if worker.poll() is None:
       try:worker.stdin.write('quit\n');worker.stdin.flush();worker.wait(timeout=2)
       except (BrokenPipeError,subprocess.TimeoutExpired):worker.kill();worker.wait(timeout=4)
      for stream in (worker.stdin,worker.stdout,worker.stderr):stream.close()
  check('no-physical-execution',True)
 return {'version':'0.6.0','mode':'offline-surviving-worker-revocation','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'limitations':['Worker is a real independent Python process but not an authorized display executor','No physical rollback, systemd integration or same-UID hostile-process isolation','No proof against an arbitrary process bypassing the cooperative writer API']}
if __name__=='__main__':
 try:print(json.dumps(run(),indent=2))
 except BaseException as exc:
  print(json.dumps({'selftest':'failed','error':type(exc).__name__+': '+str(exc),'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0}));sys.exit(2)
