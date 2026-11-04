#!/usr/bin/env python3
"""Offline negative/positive matrix; no Hyprland invocation."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

p=Path(__file__).with_name('display-physical-command-contract-v0600.py')
s=importlib.util.spec_from_file_location('contract',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
raw=b'hl.monitor({output="eDP-1"})'
digest=hashlib.sha256(raw).hexdigest()
base=[{'name':'eDP-1','make':'AU Optronics','model':'B156HAN15.H ','width':1920,'height':1080,'refreshRate':60.061,'scale':1,'transform':0,'disabled':False,'x':0,'y':0,'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']},{'name':'HDMI-A-1','make':'Dell Inc.','model':'Inspiron 3043','width':1920,'height':1080,'refreshRate':60.0,'scale':1,'transform':0,'disabled':False,'x':1920,'y':0}]
def fp(a):return hashlib.sha256(json.dumps(sorted(a,key=lambda x:x['name']),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def capture(a):return {'decision':'LIVE_AUTHORITY_READONLY_READY','captureAtomic':False,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,'target':a[0]['name'],'alternate':'eDP-2' if a[0]['name']=='eDP-1' else 'eDP-1','protected':'HDMI-A-1','persistentSource':'config/monitors.lua','luaSha256':digest,'monitorDigest':fp(a),'compositor':{'pid':8297,'starttime':24639,'instance':'test-instance','socketDevice':57,'socketInode':83}}
results=[]
def check(name,mutator=None,allowed=False,edp2=False):
    a=copy.deepcopy(base)
    if edp2:a[0]['name']='eDP-2'
    c=capture(a)
    b=raw;h=digest;original=None
    if mutator:
        c,a,b,h,original=mutator(c,a,b,h,original)
    try:
        r=m.plan(c,a,b,h,original)
        passed=allowed and r['decision']=='PHYSICAL_COMMAND_CONTRACT_READONLY' and r['commandsExecuted']==0 and r['physicalExecutionEnabled'] is False and r['target']==c['target']
    except m.Denied:
        passed=not allowed
    results.append({'scenario':name,'passed':bool(passed)})
def edit_capture(field,value):
    def f(c,a,b,h,o):c[field]=value;return c,a,b,h,o
    return f
def edit_monitor(field,value):
    def f(c,a,b,h,o):a[0][field]=value;return c,a,b,h,o
    return f
def edit_comp(field,value):
    def f(c,a,b,h,o):c['compositor'][field]=value;return c,a,b,h,o
    return f
def other_lua(c,a,b,h,o):return c,a,b'changed',h,o
def stale(c,a,b,h,o):return c,a,b,h,'eDP-2'
def duplicate(c,a,b,h,o):a[1]['name']=a[0]['name'];return c,a,b,h,o
def extra(c,a,b,h,o):a.append(copy.deepcopy(a[1]));return c,a,b,h,o
def no_mode(c,a,b,h,o):a[0]['availableModes'].pop();return c,a,b,h,o
check('edp1_baseline',allowed=True)
check('edp2_baseline',allowed=True,edp2=True)
for field,value in [('decision','READY'),('captureAtomic',True),('executionAuthorized',True),('physicalExecutionEnabled',True),('commandsExecuted',1),('target','DP-1'),('alternate','eDP-1'),('protected','eDP-1'),('persistentSource','monitors.conf'),('luaSha256','0'*64),('monitorDigest','0'*64)]:check('capture_'+field,edit_capture(field,value))
for field,value in [('make','Unknown'),('model','Other'),('width',1280),('height',720),('refreshRate',144.06),('scale',1.25),('transform',1),('disabled',True),('availableModes',[])]:check('monitor_'+field,edit_monitor(field,value))
for field,value in [('pid',0),('starttime',0),('instance',''),('socketDevice',-1),('socketInode',-1)]:check('compositor_'+field,edit_comp(field,value))
for name,f in [('lua_changed',other_lua),('stale_connector',stale),('duplicate',duplicate),('extra',extra),('mode_missing',no_mode)]:check(name,f)
output={'selftest':'passed' if all(x['passed'] for x in results) else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}
print(json.dumps(output,indent=2))
raise SystemExit(0 if output['selftest']=='passed' else 2)
