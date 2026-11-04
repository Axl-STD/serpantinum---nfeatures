#!/usr/bin/env python3
import importlib.util,json
from pathlib import Path
p=Path(__file__).with_name('display-dynamic-authority-binding-v0600.py');s=importlib.util.spec_from_file_location('binding',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
plan={'decision':'DYNAMIC_READ_ONLY_PLAN_READY','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'target':'eDP-1','alternate':'eDP-2','protected':'HDMI-A-1','persistentSource':'config/monitors.lua','configurationAdapter':'hyprland','original':'1920x1080@60.06Hz','candidate':'1920x1080@144.06Hz','luaSha256':'a'*64}
record={'id':'b'*64,'deadline_ns':10000,'generation':4,'state':'APPLIED'}
side={'clock_schema':3,'recovery_required':True,'transaction_id':'b'*64,'deadline_realtime_ns':10000,'generation_at_binding':4,'boot_id':'3a652ad0-ef12-4c58-a159-671b3f4c370e','deadline_boottime_ns':5000}
comp={'pid':8297,'starttime':100,'instance':'instance','connector':'eDP-1'}
checks=[]
def check(name,expected,field=None,value=None,which='plan'):
    import copy
    a,b,c,d=map(copy.deepcopy,(plan,record,side,comp))
    obj={'plan':a,'record':b,'side':c,'temporal':c,'compositor':d}[which]
    if field is not None:obj[field]=value
    r=m.decide(a,b,c,d)
    ok=r['decision']==expected and r['physicalExecutionEnabled'] is False and r['commandsExecuted']==0
    checks.append({'scenario':name,'passed':ok})
    if not ok:raise AssertionError(name+': '+str(r))
check('valid','OFFLINE_BINDING_READY')
for f,v in [('decision','BLOCK'),('physicalExecutionEnabled',True),('executionAuthorized',True),('commandsExecuted',1),('target','DP-1'),('alternate','eDP-1'),('protected','DP-1'),('persistentSource','monitors.conf'),('configurationAdapter','sway'),('original','bad'),('candidate','bad'),('luaSha256','bad')]:check('plan-'+f,'BLOCK_PLAN' if f in ('decision','physicalExecutionEnabled','executionAuthorized','commandsExecuted') else 'BLOCK_MONITOR_IDENTITY',f,v)
for f,v in [('clock_schema',2),('recovery_required',False),('transaction_id','bad'),('deadline_realtime_ns',20000),('generation_at_binding',5)]:check('side-'+f,'BLOCK_TRANSACTION_BINDING',f,v,'temporal')
for f,v in [('boot_id','bad'),('deadline_boottime_ns',0)]:check('clock-'+f,'BLOCK_TEMPORAL',f,v,'temporal')
for f,v in [('pid',0),('starttime',0),('instance',''),('connector','eDP-2')]:check('compositor-'+f,'BLOCK_COMPOSITOR_IDENTITY',f,v,'compositor')
for f,v,e in [('state','UNCERTAIN','BLOCK_RECONCILIATION'),('state','BAD','BLOCK_STATE'),('state','CONFIRMED','OFFLINE_TERMINAL_BINDING'),('state','ROLLED_BACK','OFFLINE_TERMINAL_BINDING')]:check('record-'+v,e,f,v,'record')
check('valid-edp2','BLOCK_MONITOR_IDENTITY','target','eDP-2')
check('wrong-record-id','BLOCK_TRANSACTION_BINDING','id','bad','record')
check('wrong-generation','BLOCK_TRANSACTION_BINDING','generation',2,'record')
print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0}))
