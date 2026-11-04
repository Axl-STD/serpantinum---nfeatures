#!/usr/bin/env python3
"""Consolidated offline test of the physical rollback mock boundary."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

HERE=Path(__file__).resolve().parent
p=HERE/'display-physical-rollback-rehearsal-v0600.py'
s=importlib.util.spec_from_file_location('rehearsal',p)
m=importlib.util.module_from_spec(s)
s.loader.exec_module(m)

results=[]
def check(name, fn, ok=True):
    try:
        fn()
        passed=ok
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        passed=not ok
    results.append({'scenario':name,'passed':passed})

def fixture(target='eDP-1', hdmi=False, mode=144.061):
    root=Path(tempfile.mkdtemp(prefix='serpantinum-rehearsal-'))
    os.chmod(root,0o700)
    original={'name':target,'description':'AU Optronics B156HAN15.H','width':1920,'height':1080,
              'refreshRate':60.061,'x':0,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True}
    baseline=[original]
    if hdmi:
        baseline.append({'name':'HDMI-A-1','description':'Dell','width':1920,'height':1080,
                         'refreshRate':60.0,'x':1920,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True})
    topology='internal-and-hdmi' if hdmi else 'internal-only'
    capture={'decision':'DYNAMIC_TOPOLOGY_READONLY_READY','target':target,'topology':topology,
             'persistentSource':'config/monitors.lua','captureAtomic':False,
             'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,
             'compositor':{'instance':'instance123','pid':981,'starttime':100},
             'luaSha256':'a'*64,'monitorDigest':'b'*64}
    rec={'schema':1,'id':secrets.token_hex(32),'generation':4,'state':'APPLIED',
         'target':target,'topology':topology,'boot_id':'boot123','phase':'APPLY_VERIFIED',
         'deadline_boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1}
    side={'schema':1,'transaction_id':rec['id'],'generation_at_binding':1,
          'boot_id':rec['boot_id'],'target':target,'topology':topology,
          'snapshot':sorted(baseline,key=lambda x:x['name']),
          'snapshot_sha256':m.snap.digest(sorted(baseline,key=lambda x:x['name'])),
          'identity':m.snap.identity(capture,target,topology)}
    m.ipc.write(root,'transaction.json',rec)
    m.ipc.write(root,'physical-snapshot.json',side)
    observed=copy.deepcopy(baseline)
    for item in observed:
        if item['name']==target:item['refreshRate']=mode
    return root,capture,observed

def run(target='eDP-1',hdmi=False,mode=144.061,edit=None,fail=False,ignore=False):
    root,capture,monitors=fixture(target,hdmi,mode)
    if edit:edit(root,capture,monitors)
    return m.execute(root,capture,m.MockTransport(monitors,fail=fail,ignore=ignore))

def change_record(key,value):
    def f(root,capture,monitors):
        r=m.ipc.read(root,'transaction.json');r[key]=value;m.ipc.write(root,'transaction.json',r)
    return f

def change_side(key,value):
    def f(root,capture,monitors):
        r=m.ipc.read(root,'physical-snapshot.json');r[key]=value;m.ipc.write(root,'physical-snapshot.json',r)
    return f

def change_monitor(key,value):
    def f(root,capture,monitors):monitors[0][key]=value
    return f

def change_capture(key,value):
    def f(root,capture,monitors):capture[key]=value
    return f

check('internal_edp1',lambda:run())
check('internal_edp2',lambda:run(target='eDP-2'))
check('hdmi_edp1',lambda:run(hdmi=True))
check('hdmi_edp2',lambda:run(target='eDP-2',hdmi=True))
check('already_original',lambda:run(mode=60.061))
check('ipc_failure',lambda:run(fail=True),False)
check('ipc_ignored',lambda:run(ignore=True),False)
check('unknown_mode',lambda:run(mode=120),False)
check('topology_hotplug',lambda:run(edit=lambda r,c,ms:ms.append(copy.deepcopy(ms[0]))),False)
check('connector_drift',lambda:run(edit=change_monitor('name','eDP-9')),False)
check('resolution_drift',lambda:run(edit=change_monitor('width',1280)),False)
check('scale_drift',lambda:run(edit=change_monitor('scale',1.25)),False)
check('position_drift',lambda:run(edit=change_monitor('x',100)),False)
check('transform_drift',lambda:run(edit=change_monitor('transform',1)),False)
check('power_drift',lambda:run(edit=change_monitor('dpmsStatus',False)),False)
check('capture_drift',lambda:run(edit=change_capture('luaSha256','c'*64)),False)
check('transaction_id_drift',lambda:run(edit=change_record('id','0'*64)),False)
check('boot_drift',lambda:run(edit=change_record('boot_id','different')),False)
check('generation_drift',lambda:run(edit=change_record('generation',0)),False)
check('sidecar_hash_drift',lambda:run(edit=change_side('snapshot_sha256','0'*64)),False)
check('not_pending',lambda:run(edit=change_record('state','CONFIRMED')),False)
check('uncertain_apply',lambda:run(edit=lambda r,c,ms:(change_record('state','UNCERTAIN')(r,c,ms),change_record('phase','APPLY_INTENT')(r,c,ms))),False)
check('early_deadline',lambda:run(edit=change_record('deadline_boottime_ns',time.clock_gettime_ns(time.CLOCK_BOOTTIME)+100000000000)),False)
check('rollback_intent_original',lambda:run(mode=60.061,edit=lambda r,c,ms:(change_record('state','UNCERTAIN')(r,c,ms),change_record('phase','ROLLBACK_INTENT')(r,c,ms))))
check('rollback_intent_candidate',lambda:run(edit=lambda r,c,ms:(change_record('state','UNCERTAIN')(r,c,ms),change_record('phase','ROLLBACK_INTENT')(r,c,ms))))

assert all(r['passed'] for r in results),[r for r in results if not r['passed']]
print(json.dumps({'selftest':'passed','totalScenarios':len(results),'results':results,
                  'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
