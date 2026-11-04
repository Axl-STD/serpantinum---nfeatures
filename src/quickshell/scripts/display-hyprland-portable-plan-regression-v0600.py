#!/usr/bin/env python3
"""Offline tests for the read-only operation planner."""
import importlib.util,json,hashlib,sys,copy
from pathlib import Path
p=Path(__file__).with_name('display-hyprland-portable-plan-v0600.py')
s=importlib.util.spec_from_file_location('planner',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
base=[dict(name='eDP-1',width=1920,height=1080,x=0,y=0,scale=1,transform=0,disabled=False,refreshRate=60.061,availableModes=['1920x1080@60.06Hz','1920x1080@144.06Hz']),dict(name='HDMI-A-1',width=1920,height=1080,x=1920,y=0,scale=1,transform=0,disabled=False,refreshRate=60.,availableModes=['1920x1080@60.00Hz'])]
config=b'monitor=offline-test\n';digest=hashlib.sha256(config).hexdigest();checks=[]
def check(name,valid):
 checks.append({'scenario':name,'passed':bool(valid)})
 if not valid:raise AssertionError(name)
def denied(name,change):
 x=copy.deepcopy(base);change(x)
 try:m.plan(base,x,config,digest)
 except m.Denied:check(name,True)
 else:check(name,False)
try:
 result=m.plan(base,copy.deepcopy(base),config,digest)
 check('baseline',result['decision']=='READ_ONLY_PLAN_READY')
 check('no-execution',result['commandsExecuted']==0 and result['physicalExecutionEnabled'] is False)
 check('protected-output',result['protected']=='HDMI-A-1')
 for index in (0,1):
  for key,value in [('width',1280),('height',720),('x',999),('y',999),('scale',1.5),('transform',1),('disabled',True),('refreshRate',75.)]:
   denied(f'{index}-{key}',lambda x,i=index,k=key,v=value:x[i].__setitem__(k,v))
 denied('missing-mode',lambda x:x[0].__setitem__('availableModes',[]))
 denied('extra-monitor',lambda x:x.append(dict(name='DP-3')))
 denied('duplicate-monitor',lambda x:x[1].__setitem__('name','eDP-1'))
 denied('nan-refresh',lambda x:x[0].__setitem__('refreshRate',float('nan')))
 try:m.plan(base,base,b'changed',digest)
 except m.Denied:check('digest',True)
 else:check('digest',False)
 print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}))
except Exception as exc:
 print(json.dumps({'selftest':'failed','error':str(exc),'results':checks}));sys.exit(2)
