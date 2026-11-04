#!/usr/bin/env python3
"""Offline contract and persistence tests. No compositor or physical commands."""
import importlib.util,json,sys,tempfile,time,copy
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('live_test',HERE/'display-live-recovery-supervisor-v0600.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
c=m.load('display-physical-recovery-controller-v0600.py');t=m.load('display-temporal-schema3-v0600.py')
checks=[]
def check(label,ok):
 checks.append({'scenario':label,'passed':bool(ok)})
 if not ok:raise AssertionError(label)
def rejected(value):
 try:m.validate(value);return False
 except (RuntimeError,ValueError,TypeError,KeyError):return True
base={'decision':'LIVE_AUTHORITY_READONLY_READY','physicalExecutionEnabled':False,'executionAuthorized':False,'captureAtomic':False,'commandsExecuted':0,'target':'eDP-1','alternate':'eDP-2','protected':'HDMI-A-1','persistentSource':'config/monitors.lua','luaSha256':'a'*64,'monitorDigest':'b'*64,'compositor':{'pid':100,'starttime':123,'instance':'instance','socketDevice':57,'socketInode':83}}
check('valid',m.validate(base)==base)
for key,val in [('decision','BLOCK'),('physicalExecutionEnabled',True),('executionAuthorized',True),('captureAtomic',True),('commandsExecuted',1),('commandsExecuted',False),('target','HDMI-A-1'),('alternate','eDP-1'),('protected','eDP-2'),('persistentSource','monitors.conf'),('luaSha256','bad'),('monitorDigest','bad'),('compositor',None)]:
 x=copy.deepcopy(base);x[key]=val;check('reject-'+key+'-'+str(val)[:12],rejected(x))
for key,val in [('pid',0),('pid','100'),('starttime',0),('instance',''),('socketDevice','57'),('socketInode',None)]:
 x=copy.deepcopy(base);x['compositor'][key]=val;check('reject-compositor-'+key,rejected(x))
x=copy.deepcopy(base);x['target']='eDP-2';x['alternate']='eDP-1';check('alternate-valid',m.validate(x)==x)
with tempfile.TemporaryDirectory(prefix='serpantinum-live-binding-test-') as tmp:
 root=Path(tmp);root.chmod(0o700)
 snapshot={'name':'eDP-1','width':1920,'height':1080,'refresh_mhz':60060,'scale_milli':1000,'transform':0}
 r=c.prepare(root,snapshot,time.time_ns()+60_000_000_000)
 t.bind(root)
 result=m.bind(root,base);check('bind',result['decision']=='LIVE_BINDING_STORED')
 check('inspect',m.inspect(root,base)['decision']=='LIVE_BINDING_CONSISTENT')
 try:m.bind(root,base);duplicate=False
 except RuntimeError:duplicate=True
 check('duplicate-denied',duplicate)
 x=copy.deepcopy(base);x['compositor']['pid']=101
 try:m.inspect(root,x);changed=False
 except RuntimeError:changed=True
 check('changed-pid-denied',changed)
 x=copy.deepcopy(base);x['luaSha256']='c'*64
 try:m.inspect(root,x);changed=False
 except RuntimeError:changed=True
 check('changed-lua-denied',changed)
 x=copy.deepcopy(base);x['monitorDigest']='d'*64
 try:m.inspect(root,x);changed=False
 except RuntimeError:changed=True
 check('changed-monitor-denied',changed)
 a=c.transition(root,r['id'],r['generation'],'ARMED');check('generation-advances',m.inspect(root,base)['generation']==a['generation'])
 check('binding-file', (root/'live-authority-binding.json').is_file())
 check('no-physical-execution',result['commandsExecuted']==0 and result['physicalExecutionEnabled'] is False)
print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
