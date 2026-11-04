#!/usr/bin/env python3
"""Read-only live capture bound to offline recovery; no physical operations."""
import argparse, hashlib, importlib.util, json, os, pathlib, sys
HERE=pathlib.Path(__file__).resolve().parent
class Denied(RuntimeError): pass
def load(name):
 p=HERE/name
 if p.is_symlink() or not p.is_file(): raise Denied('dependency')
 spec=importlib.util.spec_from_file_location('live_'+name.replace('-','_').replace('.','_'),p)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def validate(capture):
 if not isinstance(capture,dict) or capture.get('decision')!='LIVE_AUTHORITY_READONLY_READY':raise Denied('capture')
 if any(capture.get(k) is not False for k in ('physicalExecutionEnabled','executionAuthorized','captureAtomic')):raise Denied('authority')
 if type(capture.get('commandsExecuted')) is not int or capture['commandsExecuted']!=0:raise Denied('commands')
 if capture.get('target') not in ('eDP-1','eDP-2') or capture.get('alternate')==capture['target'] or {capture['target'],capture['alternate']}!={'eDP-1','eDP-2'}:raise Denied('connector')
 if capture.get('protected')!='HDMI-A-1' or capture.get('persistentSource')!='config/monitors.lua':raise Denied('source')
 if not isinstance(capture.get('luaSha256'),str) or len(capture['luaSha256'])!=64:raise Denied('lua')
 if not isinstance(capture.get('monitorDigest'),str) or len(capture['monitorDigest'])!=64:raise Denied('monitors')
 c=capture.get('compositor')
 if not isinstance(c,dict) or type(c.get('pid')) is not int or c['pid']<2 or type(c.get('starttime')) is not int or c['starttime']<1 or not isinstance(c.get('instance'),str) or not c['instance'] or any(type(c.get(k)) is not int for k in ('socketDevice','socketInode')):raise Denied('compositor')
 return capture

def observe(hypr_dir,lua_backup,config_backup):
 cap=load('display-live-authority-capture-v0600.py')
 return validate(cap.capture(hypr_dir,lua_backup,config_backup))

def bind(root,capture):
 c=load('display-physical-recovery-controller-v0600.py');t=load('display-temporal-schema3-v0600.py')
 validate(capture)
 with c._locked(root) as path:
  r=c._read(path);s=t._read(path)
  if not r or not s or r['state'] not in ('PREPARED','ARMED','APPLIED') or r['id']!=s['transaction_id'] or r['deadline_ns']!=s['deadline_realtime_ns'] or s['boot_id']!=t.boot_id():raise Denied('transaction')
  destination=path/'live-authority-binding.json'
  if destination.exists() or destination.is_symlink():raise Denied('already bound')
  data={'schema':1,'transaction_id':r['id'],'generation_at_binding':r['generation'],'boot_id':s['boot_id'],'capture':capture,'capture_sha256':hashlib.sha256(canonical(capture)).hexdigest()}
  import secrets
  tmp=path/('.live-binding-'+secrets.token_hex(8))
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  try:
   with os.fdopen(fd,'wb') as f:f.write(canonical(data));f.flush();os.fsync(f.fileno())
   os.rename(tmp,destination)
   dfd=os.open(path,os.O_RDONLY|os.O_DIRECTORY);os.fsync(dfd);os.close(dfd)
  finally:
   if tmp.exists():tmp.unlink()
 return {'decision':'LIVE_BINDING_STORED','transactionId':r['id'],'physicalExecutionEnabled':False,'commandsExecuted':0}

def inspect(root,capture):
 c=load('display-physical-recovery-controller-v0600.py');t=load('display-temporal-schema3-v0600.py')
 validate(capture)
 with c._locked(root) as path:
  r=c._read(path);s=t._read(path)
  file=path/'live-authority-binding.json'
  if file.is_symlink() or not file.is_file() or file.stat().st_size>16384:raise Denied('binding missing')
  data=json.loads(file.read_text())
  if set(data)!={'schema','transaction_id','generation_at_binding','boot_id','capture','capture_sha256'} or data['schema']!=1 or not r or not s:raise Denied('binding invalid')
  if data['capture_sha256']!=hashlib.sha256(canonical(data['capture'])).hexdigest() or data['capture']!=capture:raise Denied('live identity changed')
  if data['transaction_id']!=r['id'] or data['transaction_id']!=s['transaction_id'] or data['boot_id']!=s['boot_id'] or data['boot_id']!=t.boot_id() or r['generation']<data['generation_at_binding']:raise Denied('transaction changed')
  return {'decision':'LIVE_BINDING_TERMINAL' if r['state'] in ('ROLLED_BACK','CONFIRMED') else 'LIVE_BINDING_CONSISTENT','state':r['state'],'generation':r['generation'],'physicalExecutionEnabled':False,'commandsExecuted':0}

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['bind','inspect']);p.add_argument('root');p.add_argument('--hypr-dir',required=True);p.add_argument('--lua-backup',required=True);p.add_argument('--config-backup',required=True);a=p.parse_args()
 try:
  live=observe(a.hypr_dir,a.lua_backup,a.config_backup)
  out=bind(a.root,live) if a.action=='bind' else inspect(a.root,live);rc=0
 except (OSError,RuntimeError,ValueError,TypeError,KeyError) as e:
  out={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(e).__name__,'physicalExecutionEnabled':False,'commandsExecuted':0};rc=2
 print(json.dumps(out,indent=2));return rc
if __name__=='__main__':sys.exit(main())
