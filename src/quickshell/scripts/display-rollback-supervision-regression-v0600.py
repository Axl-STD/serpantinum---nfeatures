#!/usr/bin/env python3
"""Offline regression for unified mock rollback supervision."""
import copy
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('supervision', HERE/'display-rollback-supervision-v0600.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]

def fixture(target='eDP-1', hdmi=False, mode=144.061):
    root=Path(tempfile.mkdtemp(prefix='serpantinum-supervision-'))
    original={'name':target,'description':'AU Optronics B156HAN15.H','width':1920,'height':1080,'refreshRate':60.061,'x':0,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True}
    baseline=[original]
    if hdmi:
        baseline.append({'name':'HDMI-A-1','description':'Dell','width':1920,'height':1080,'refreshRate':60.0,'x':1920,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True})
    topology='internal-and-hdmi' if hdmi else 'internal-only'
    capture={'decision':'DYNAMIC_TOPOLOGY_READONLY_READY','target':target,'topology':topology,'persistentSource':'config/monitors.lua','captureAtomic':False,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'compositor':{'instance':'instance123','pid':981,'starttime':100},'luaSha256':'a'*64,'monitorDigest':'b'*64}
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    record={'schema':1,'id':secrets.token_hex(32),'generation':4,'state':'APPLIED','target':target,'topology':topology,'boot_id':boot,'phase':'APPLY_VERIFIED','deadline_boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1}
    baseline=sorted(baseline,key=lambda x:x['name'])
    side={'schema':1,'transaction_id':record['id'],'generation_at_binding':1,'boot_id':boot,'target':target,'topology':topology,'snapshot':baseline,'snapshot_sha256':m.rehearsal.snap.digest(baseline),'identity':m.rehearsal.snap.identity(capture,target,topology)}
    m.ipc.write(root,'transaction.json',record)
    m.ipc.write(root,'physical-snapshot.json',side)
    m.ipc.write(root,'simulated-ipc.json',{'connector':target,'mode':m.ipc.TARGET})
    observed=copy.deepcopy(baseline)
    for item in observed:
        if item['name']==target:item['refreshRate']=mode
    return root,capture,observed

def test(name, *, target='eDP-1',hdmi=False,mode=144.061,mutation=None, expected='MOCK_AND_IPC_RECOVERY_VERIFIED'):
    root,capture,monitors=fixture(target,hdmi,mode)
    if mutation: mutation(root,capture,monitors)
    result=m.execute(root,capture,monitors)
    ok=result['decision']==expected and result['commandsExecuted']==0 and result['rollbackPhysicalVerified'] is False
    if expected=='MOCK_AND_IPC_RECOVERY_VERIFIED':
        r=m.ipc.read(root,'transaction.json')
        ok=ok and r['state']=='ROLLED_BACK' and r['phase']=='COMPLETED'
    results.append({'scenario':name,'passed':bool(ok),'decision':result['decision']})

def mutate(key,value):
    def fn(root,capture,monitors):
        r=m.ipc.read(root,'transaction.json');r[key]=value;m.ipc.write(root,'transaction.json',r)
    return fn

test('edp1')
test('edp2',target='eDP-2')
test('hdmi_edp1',hdmi=True)
test('hdmi_edp2',target='eDP-2',hdmi=True)
test('already_original',mode=60.061)
test('not_due',mutation=mutate('deadline_boottime_ns',time.clock_gettime_ns(time.CLOCK_BOOTTIME)+100000000000),expected='WAITING')
test('wrong_boot',mutation=mutate('boot_id','other'),expected='BLOCK_NEW_BOOT')
test('uncertain_apply',mutation=lambda r,c,ms:(mutate('state','UNCERTAIN')(r,c,ms),mutate('phase','APPLY_INTENT')(r,c,ms)),expected='BLOCK_AMBIGUOUS_APPLY')
test('geometry_drift',mutation=lambda r,c,ms:ms[0].update(width=1280),expected='BLOCK_REHEARSAL')
test('lua_drift',mutation=lambda r,c,ms:c.update(luaSha256='c'*64),expected='BLOCK_REHEARSAL')
test('confirmed',mutation=mutate('state','CONFIRMED'),expected='BLOCK_STATE')
test('terminal',mutation=mutate('state','ROLLED_BACK'),expected='BLOCK_ALREADY_TERMINAL_REQUIRES_PHYSICAL_VERIFICATION')
assert all(x['passed'] for x in results),results
print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
