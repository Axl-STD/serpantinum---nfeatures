#!/usr/bin/env python3
"""Offline phase journal; never grants physical execution."""
import hashlib, importlib.util, json, os, secrets, stat
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(name,alias):
 p=HERE/name
 if p.is_symlink() or not p.is_file(): raise RuntimeError('unsafe dependency')
 s=importlib.util.spec_from_file_location(alias,p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
c=load('display-physical-recovery-controller-v0600.py','phase_controller')
sim=load('display-recovery-simulator-v0600.py','phase_simulator')
PHASES={'APPLY_INTENT','APPLY_EFFECT_OBSERVED','ROLLBACK_INTENT','ROLLBACK_EFFECT_OBSERVED','COMPLETED'}

def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def read(root):
 p=root/'operation-phase.json'
 if p.is_symlink():raise RuntimeError('symlink journal')
 try:fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 except FileNotFoundError:return None
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1 or st.st_size>8192:raise RuntimeError('unsafe journal')
  with os.fdopen(fd,'r',closefd=False) as f:v=json.load(f)
  if not isinstance(v,dict) or set(v)!={'schema','transaction_id','generation','phase','boot_id','observed_digest'} or v['schema']!=1 or v['phase'] not in PHASES or type(v['generation']) is not int or v['generation']<1 or not isinstance(v['transaction_id'],str) or not isinstance(v['boot_id'],str) or not isinstance(v['observed_digest'],str):raise RuntimeError('invalid journal')
  return v
 finally:os.close(fd)

def write(root,record,phase,observed):
 if phase not in PHASES:raise RuntimeError('invalid phase')
 p=root/'operation-phase.json'
 if p.is_symlink():raise RuntimeError('symlink journal')
 data={'schema':1,'transaction_id':record['id'],'generation':record['generation'],'phase':phase,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'observed_digest':digest(observed)}
 temp=root/('.phase-'+secrets.token_hex(12))
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  with os.fdopen(fd,'w') as f:json.dump(data,f,sort_keys=True);f.flush();os.fsync(f.fileno())
  os.replace(temp,p)
  dfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:os.fsync(dfd)
  finally:os.close(dfd)
 finally:
  if temp.exists():temp.unlink()
 return data

def operation(root,action,token,generation,fail_after_effect=False):
 if action not in ('apply','rollback'):raise RuntimeError('invalid action')
 with c._locked(root) as path:
  record=c._read(path)
  sim._checked(record,token,generation,'ARMED' if action=='apply' else 'ROLLBACK_REQUIRED')
  observed=sim._observed(path)
  if record['snapshot']!=sim.BASE or observed not in (sim.BASE,sim.TARGET):raise RuntimeError('invalid snapshot')
  if action=='apply' and observed!=sim.BASE:raise RuntimeError('invalid apply observation')
  if action=='apply':
   import time
   if time.time_ns()>=record['deadline_ns']:raise RuntimeError('expired')
  if read(path) is not None:raise RuntimeError('journal already exists')
  write(path,record,'APPLY_INTENT' if action=='apply' else 'ROLLBACK_INTENT',observed)
  uncertain=dict(record,state='UNCERTAIN',generation=generation+1);c._write(path,uncertain)
  target=sim.TARGET if action=='apply' else sim.BASE
  sim._write_observed(path,target)
  if fail_after_effect:raise RuntimeError('injected crash window')
  write(path,uncertain,'APPLY_EFFECT_OBSERVED' if action=='apply' else 'ROLLBACK_EFFECT_OBSERVED',sim._observed(path))
  if sim._observed(path)!=target:raise RuntimeError('verification failed')
  final=dict(uncertain,state='APPLIED' if action=='apply' else 'ROLLED_BACK',generation=uncertain['generation']+1)
  c._write(path,final)
  write(path,final,'COMPLETED',target)
  return final

def audit(root):
 with c._locked(root) as path:
  r=c._read(path);j=read(path);observed=sim._observed(path)
  if r is None or j is None:return {'decision':'BLOCK_MISSING_JOURNAL','physicalExecutionEnabled':False,'commandsExecuted':0}
  if j['transaction_id']!=r['id'] or j['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip() or j['generation']>r['generation']:decision='BLOCK_IDENTITY'
  elif r['state']=='UNCERTAIN':decision='RECONCILIATION_REQUIRED'
  elif j['phase']=='COMPLETED' and r['state'] in ('APPLIED','ROLLED_BACK') and j['generation']==r['generation'] and j['observed_digest']==digest(observed):decision='TERMINAL_VERIFIED' if r['state']=='ROLLED_BACK' else 'APPLY_VERIFIED'
  else:decision='BLOCK_INCONSISTENT'
  return {'decision':decision,'phase':j['phase'],'state':r['state'],'generation':r['generation'],'physicalExecutionEnabled':False,'commandsExecuted':0}
