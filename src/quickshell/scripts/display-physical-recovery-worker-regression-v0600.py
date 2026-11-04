#!/usr/bin/env python3
import importlib.util,json,tempfile,time,secrets,copy,concurrent.futures
from pathlib import Path
D=Path(__file__).resolve().parent

def load(name,alias):
 p=D/name;s=importlib.util.spec_from_file_location(alias,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
w=load('display-physical-recovery-worker-v0600.py','test_worker')
ipc=w.ipc
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
results=[]
def test(name,fn):
 try:fn();results.append(dict(scenario=name,passed=True))
 except Exception as e:results.append(dict(scenario=name,passed=False,error=repr(e)))

def setup(state='APPLIED',mode=144.06,topology='internal-only'):
 t=tempfile.TemporaryDirectory();root=Path(t.name);root.chmod(0o700)
 target='eDP-1';snap=[dict(name=target,description='AU Optronics',width=1920,height=1080,refreshRate=60.061,x=0,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True)]
 if topology=='internal-and-hdmi':snap.append(dict(name='HDMI-A-1',description='Dell',width=1920,height=1080,refreshRate=60.0,x=1920,y=0,scale=1,transform=0,disabled=False,dpmsStatus=True))
 record=dict(schema=1,id=secrets.token_hex(32),generation=4,state=state,target=target,topology=topology,plan_sha256='b'*64,boot_id=boot,deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1000000,phase='APPLY_VERIFIED',execution_backend='physical')
 side=dict(transaction_id=record['id'],boot_id=boot,target=target,topology=topology,snapshot=snap,snapshot_sha256=w.sha(snap),identity={'instance':'instance_123'})
 with ipc.locked(root) as p:ipc.write(p,'transaction.json',record);ipc.write(p,'physical-snapshot.json',side)
 keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
 with ipc.locked(root) as p:ipc.write(p,'physical-watchdog-rehearsal.json',dict(transaction_id=record['id'],transaction_digest=w.sha({k:record[k] for k in keys}),status='ARMED_LAB_ONLY'))
 obs=copy.deepcopy(snap);obs[0]['refreshRate']=mode
 return t,root,w.LabTransport(obs,'instance_123')

def scenario(kind):
 t,root,tr=setup(topology='internal-and-hdmi' if kind=='hdmi' else 'internal-only')
 try:
  if kind=='normal' or kind=='hdmi':
   assert w.recover_lab(root,tr)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
   assert w.recover_lab(root,tr)['decision']=='LAB_TERMINAL_VERIFIED'
  elif kind=='after-intent':
   assert w.recover_lab(root,tr,failpoint='after-intent')['state']=='UNCERTAIN'
   assert w.recover_lab(root,tr)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
  elif kind=='after-effect':
   assert w.recover_lab(root,tr,failpoint='after-effect')['state']=='UNCERTAIN'
   assert w.recover_lab(root,tr)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
  elif kind=='ignored':
   tr.fail=True;assert w.recover_lab(root,tr)['decision']=='LAB_RECOVERY_NOT_VERIFIED'
   assert ipc.read(root,'transaction.json')['state']=='UNCERTAIN'
  elif kind=='instance':
   tr.instance='other';assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='hotplug':
   tr.monitors.append(dict(tr.monitors[0],name='DP-1'));assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='geometry':
   tr.monitors[0]['scale']=2;assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='unknown':
   tr.monitors[0]['refreshRate']=75;assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='simulated':
   with ipc.locked(root) as p:
    r=ipc.read(p,'transaction.json');r['execution_backend']='simulated';ipc.write(p,'transaction.json',r)
   assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='receipt':
   with ipc.locked(root) as p:
    receipt=ipc.read(p,'physical-watchdog-rehearsal.json');receipt['status']='FAKE';ipc.write(p,'physical-watchdog-rehearsal.json',receipt)
   assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='snapshot':
   with ipc.locked(root) as p:
    s=ipc.read(p,'physical-snapshot.json');s['snapshot'][0]['x']=99;ipc.write(p,'physical-snapshot.json',s)
   assert_denied(lambda:w.recover_lab(root,tr))
  elif kind=='concurrent':
   with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    outputs=list(pool.map(lambda _:w.recover_lab(root,tr)['decision'],range(4)))
   assert outputs.count('LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL')==1
   assert outputs.count('LAB_TERMINAL_VERIFIED')==3
  elif kind=='original':
   tr.monitors[0]['refreshRate']=60.061
   assert w.recover_lab(root,tr)['decision']=='LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL'
   assert tr.calls==0
  elif kind=='notdue':
   assert w.recover_lab(root,tr,now_ns=0)['decision']=='WAITING'
  elif kind=='foreign':
   assert_denied(lambda:w.recover_lab(root,object()))
  elif kind=='badphase':
   with ipc.locked(root) as p:
    r=ipc.read(p,'transaction.json');r['phase']='APPLY_INTENT';ipc.write(p,'transaction.json',r)
   assert_denied(lambda:w.recover_lab(root,tr))
 finally:t.cleanup()

def assert_denied(fn):
 try:fn()
 except (w.Denied,ipc.Denied):return
 raise AssertionError('expected denial')

for kind in ('normal','hdmi','after-intent','after-effect','ignored','instance','hotplug','geometry','unknown','simulated','receipt','snapshot','concurrent','original','notdue','foreign','badphase'):
 test(kind,lambda kind=kind:scenario(kind))

def arm_case():
 t,root,tr=setup(state='ARMED',mode=60.061)
 try:
  with ipc.locked(root) as p:
   r=ipc.read(p,'transaction.json');r['phase']='NONE';r['deadline_boottime_ns']=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+10_000_000_000;ipc.write(p,'transaction.json',r);(p/'physical-watchdog-rehearsal.json').unlink()
  assert w.arm_lab(root)['decision']=='LAB_WATCHDOG_RECEIPT_ARMED'
  assert_denied(lambda:w.arm_lab(root))
 finally:t.cleanup()
test('arm_lab_receipt',arm_case)

# Additional adversarial matrix, all on isolated physical-backend fixtures.
def mutate_case(field,value,where='monitor'):
 t,root,tr=setup()
 try:
  if where=='monitor':tr.monitors[0][field]=value
  elif where=='transaction':
   with ipc.locked(root) as p:
    r=ipc.read(p,'transaction.json');r[field]=value;ipc.write(p,'transaction.json',r)
  elif where=='snapshot':
   with ipc.locked(root) as p:
    s=ipc.read(p,'physical-snapshot.json');s[field]=value;ipc.write(p,'physical-snapshot.json',s)
  assert_denied(lambda:w.recover_lab(root,tr))
 finally:t.cleanup()

for label,field,value,where in (
 ('width_drift','width',1600,'monitor'),
 ('height_drift','height',900,'monitor'),
 ('x_drift','x',50,'monitor'),
 ('y_drift','y',20,'monitor'),
 ('transform_drift','transform',1,'monitor'),
 ('dpms_drift','dpmsStatus',False,'monitor'),
 ('disabled_drift','disabled',True,'monitor'),
 ('panel_drift','description','Other panel','monitor'),
 ('target_drift','target','eDP-2','transaction'),
 ('topology_drift','topology','internal-and-hdmi','transaction'),
 ('boot_drift','boot_id','other-boot','transaction'),
 ('token_drift','transaction_id','different','snapshot'),
 ('hash_drift','snapshot_sha256','0'*64,'snapshot'),
 ('identity_missing','identity',{},'snapshot'),
):test(label,lambda field=field,value=value,where=where:mutate_case(field,value,where))

passed=all(x['passed'] for x in results)
print(json.dumps(dict(selftest='passed' if passed else 'failed',totalScenarios=len(results),results=results,physicalExecutionEnabled=False,commandsExecuted=0),indent=2))
raise SystemExit(0 if passed else 1)
