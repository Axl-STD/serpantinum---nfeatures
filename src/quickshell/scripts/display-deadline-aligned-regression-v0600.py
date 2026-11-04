#!/usr/bin/env python3
"""Offline deterministic contract regression for deadline-aligned scheduler and worker."""
import importlib.util,json,sys,time,types,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(name,alias):
 s=importlib.util.spec_from_file_location(alias,HERE/name);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
scheduler=load('display-deadline-aligned-watchdog-v0600.py','test_scheduler')
worker=load('display-deadline-aligned-worker-v0600.py','test_worker')
results=[]
def check(label, condition):
 results.append({'scenario':label,'passed':bool(condition)})
 if not condition:raise AssertionError(label)

with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp)
 base={'name':'eDP-1','width':1920,'height':1080,'refresh_mhz':60060,'scale_milli':1000,'transform':0}
 state={'state':'APPLIED','snapshot':base,'deadline_ns':10_000_000_000,'id':'a'*64,'generation':4}
 effect={'simulatedMonitor':'TARGET'}
 class C:
  def _root(self,p):return Path(p)
  def inspect(self,p):return dict(state) if state else None
 class Sim:BASE=base;TARGET='TARGET'
 integration=types.SimpleNamespace(c=C(),sim=Sim(),inspect=lambda p:effect)
 original=scheduler.dependency
 scheduler.dependency=lambda *a:integration
 unit='serpantinum-v0600-deadline-abcdefgh'
 def attempt(label, fn):
  try:fn();ok=False
  except scheduler.DeadlineDenied:ok=True
  check(label,ok)
 try:
  for n in (3,5,8,15,30,60,120,300,599):
   state['deadline_ns']=n*1_000_000_000
   cmd=scheduler.plan(root,unit,now_ns=0)
   check('plan-'+str(n),cmd[0]=='systemd-run' and '--timer-property=AccuracySec=100ms' in cmd and '--on-active='+str(n-1)+'s' in cmd)
  for n in (0,1,2,601,1000):
   state['deadline_ns']=n*1_000_000_000
   attempt('margin-'+str(n),lambda:scheduler.plan(root,unit,now_ns=0))
  state['deadline_ns']=20_000_000_000
  for name in ('bad','serpantinum-v0600-deadline-x','serpantinum-v0600-deadline-abcdefg!','serpantinum-v0600-deadline-'+('a'*41)):
   attempt('unit-'+str(len(name)),lambda:scheduler.plan(root,name,now_ns=0))
  for status in ('PREPARED','ARMED','CONFIRMED','ROLLED_BACK','ROLLBACK_REQUIRED','UNCERTAIN'):
   state['state']=status
   attempt('state-'+status,lambda:scheduler.plan(root,unit,now_ns=0))
  state['state']='APPLIED'
  state['snapshot']={**base,'refresh_mhz':144060}
  attempt('snapshot-mismatch',lambda:scheduler.plan(root,unit,now_ns=0))
  state['snapshot']=base
  effect['simulatedMonitor']='OTHER'
  attempt('effect-mismatch',lambda:scheduler.plan(root,unit,now_ns=0))
  effect['simulatedMonitor']='TARGET'
  class Result:returncode=0
  captured=[]
  def fake_run(cmd,**kwargs):captured.append((cmd,kwargs));return Result()
  # Inject deterministic now via time.time_ns to test schedule without live systemd.
  real_time=scheduler.time.time_ns
  scheduler.time.time_ns=lambda:0
  try:
   value=scheduler.schedule(root,unit,runner=fake_run)
   check('schedule-contract',value['decision']=='DEADLINE_WATCHDOG_SCHEDULED' and value['commandsExecuted']==0)
   check('schedule-command',len(captured)==1 and captured[0][0][0]=='systemd-run')
   check('schedule-timeout',captured[0][1]['timeout']==15)
   class Bad:returncode=1
   attempt('schedule-failure',lambda:scheduler.schedule(root,unit,runner=lambda *a,**kw:Bad()))
  finally:scheduler.time.time_ns=real_time
 finally:scheduler.dependency=original

 # Worker tests use fake controller and rollback; no systemd or display calls.
 class WorkerC:
  def _root(self,p):return Path(p)
  def inspect(self,p):return dict(state) if state else None
 class Rollback:
  c=WorkerC()
  def execute(self,p):return {'decision':'SIMULATED_ROLLBACK_VERIFIED'}
 original=worker.load
 worker.load=lambda *a:Rollback()
 try:
  state={'state':'APPLIED','snapshot':base,'deadline_ns':1_000_000_000,'id':'a'*64,'generation':4}
  clock=[0];ticks=[]
  def now():return clock[0]
  def sleep(n):ticks.append(n);clock[0]+=max(1,int(n*1e9))
  result=worker.execute(root,now=now,sleep=sleep)
  check('worker-early-eventually-restores',result['decision']=='SIMULATED_ROLLBACK_VERIFIED')
  check('worker-bounded-polls',bool(ticks) and all(0<x<=0.25 for x in ticks))
  for status in ('PREPARED','ARMED','UNCERTAIN'):
   state['state']=status
   check('worker-reject-'+status,worker.execute(root,now=now,sleep=sleep)['decision']=='BLOCK_UNSAFE_STATE')
  for status in ('CONFIRMED','ROLLED_BACK'):
   state['state']=status
   check('worker-terminal-'+status,worker.execute(root,now=now,sleep=sleep)['decision']=='SIMULATED_ROLLBACK_VERIFIED')
  state['state']='APPLIED';state['deadline_ns']=700_000_000_000;clock[0]=0
  check('worker-long-wait-rejected',worker.execute(root,now=now,sleep=sleep)['decision']=='BLOCK_EXCESSIVE_WAIT')
  state['deadline_ns']=1_000_000_000;clock[0]=0
  def change_identity(n):state['id']='b'*64;clock[0]+=int(n*1e9)
  check('worker-identity-fence',worker.execute(root,now=now,sleep=change_identity)['decision']=='BLOCK_IDENTITY_CHANGED')
  state['id']='a'*64;clock[0]=0
  def change_deadline(n):state['deadline_ns']=2_000_000_000;clock[0]+=int(n*1e9)
  check('worker-deadline-fence',worker.execute(root,now=now,sleep=change_deadline)['decision']=='BLOCK_IDENTITY_CHANGED')
  state=None
  check('worker-no-state',worker.execute(root,now=now,sleep=sleep)['decision']=='BLOCK_NO_STATE')
 finally:worker.load=original
print(json.dumps({'version':'0.6.0','mode':'offline-deadline-alignment','selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
