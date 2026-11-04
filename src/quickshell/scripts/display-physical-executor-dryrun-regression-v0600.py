#!/usr/bin/env python3
"""Offline regression for read-only physical admission gate."""
import importlib.util, pathlib, json, hashlib, sys
p=pathlib.Path(__file__).with_name('display-physical-executor-dryrun-v0600.py')
s=importlib.util.spec_from_file_location('drygate',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
base={'name':'eDP-1','width':1920,'height':1080,'scale':1,'transform':0,'x':0,'y':0,'disabled':False,'refreshRate':60.061,'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']}
hdmi=dict(base,name='HDMI-A-1',x=1920,refreshRate=60.0,availableModes=['1920x1080@60.00Hz'])
a=[base,hdmi]; conf=b'example';digest=hashlib.sha256(conf).hexdigest(); checks=[]
def check(label,okay):
 checks.append({'scenario':label,'passed':bool(okay)})
 if not okay:raise AssertionError(label)
def denied(label,snapshot=None,observed=None,config=conf,sha=digest):
 try:m.validate(a if snapshot is None else snapshot,a if observed is None else observed,config,sha)
 except (m.Denied,ValueError,TypeError):check(label,True)
 else:check(label,False)
def change(name,key,value):
 b=[dict(x) for x in a];next(x for x in b if x['name']==name)[key]=value;return b
try:
 result=m.validate(a,a,conf,digest)
 check('positive',result['decision']=='DRY_RUN_READY')
 check('no-execution',result['commandsExecuted']==0)
 check('no-authorization',result['executionAuthorized'] is False)
 check('no-rollback-claim',result['rollbackVerified'] is False)
 denied('hash',sha='0'*64)
 denied('config-bytes',config=b'changed')
 denied('missing-edp',observed=[hdmi])
 denied('missing-hdmi',observed=[base])
 denied('extra-monitor',observed=a+[dict(base,name='DP-3')])
 denied('duplicate-monitor',observed=[base,base])
 for key,value in [('width',1280),('height',720),('scale',1.25),('transform',1),('x',100),('y',50),('disabled',True),('refreshRate',144.06)]:
  denied('edp-'+key,observed=change('eDP-1',key,value))
 for key,value in [('width',1280),('height',720),('scale',1.25),('transform',1),('x',100),('y',50),('disabled',True),('refreshRate',59.0)]:
  denied('hdmi-'+key,observed=change('HDMI-A-1',key,value))
 denied('missing-mode',observed=change('eDP-1','availableModes',[]))
 denied('invalid-array',snapshot={})
 check('read-only-source',all(x not in p.read_text() for x in ('hyprctl keyword','hyprctl dispatch','os.system(')))
 print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
 print(json.dumps({'selftest':'failed','error':str(exc),'results':checks}));sys.exit(2)
