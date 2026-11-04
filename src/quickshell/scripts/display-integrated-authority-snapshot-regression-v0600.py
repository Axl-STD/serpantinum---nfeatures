#!/usr/bin/env python3
import importlib.util,json,tempfile,time,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('integrated',HERE/'display-integrated-authority-snapshot-v0600.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
checks=[]
def check(label,condition):
    checks.append({'scenario':label,'passed':bool(condition)})
    if not condition:raise AssertionError(label)
plan={'decision':'DYNAMIC_READ_ONLY_PLAN_READY','target':'eDP-1','alternate':'eDP-2','protected':'HDMI-A-1','persistentSource':'config/monitors.lua','luaSha256':'a'*64,'original':'1920x1080@60.06Hz','candidate':'1920x1080@144.06Hz','configurationAdapter':'hyprland','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
comp={'pid':123,'starttime':456,'instance':'instance-test','connector':'eDP-1'}
boot='3a652ad0-ef12-4c58-a159-671b3f4c370e'
with tempfile.TemporaryDirectory() as td:
    root=Path(td)/'transaction';root.mkdir(mode=0o700)
    realtime=1_000_000_000_000
    boottime=2_000_000_000_000
    record={'schema':1,'id':'a'*64,'state':'APPLIED','deadline_ns':realtime+10_000_000_000,'snapshot':{'name':'eDP-1','width':1920,'height':1080,'refresh_mhz':60060,'scale_milli':1000,'transform':0},'generation':4}
    side={'clock_schema':3,'boot_id':boot,'transaction_id':record['id'],'generation_at_binding':4,'deadline_boottime_ns':boottime+10_000_000_000,'deadline_realtime_ns':record['deadline_ns'],'recovery_required':True}
    def write():
        m.controller._write(root,record)
        (root/'temporal-schema3.json').write_text(json.dumps(side))
        (root/'temporal-schema3.json').chmod(0o600)
    write()
    def run(p=None,c=None,b=None,now=None):
        return m.snapshot(root,plan if p is None else p,comp if c is None else c,{'realtime_ns':realtime,'boottime_ns':boottime} if now is None else now,boot if b is None else b)
    check('waiting',run()['decision']=='OFFLINE_WAITING')
    check('read-only',run()['commandsExecuted']==0)
    check('no-authorization',run()['executionAuthorized'] is False)
    check('no-physical',run()['physicalExecutionEnabled'] is False)
    check('atomic-record-read',run()['captureAtomicForTransaction'] is True)
    check('no-compositor-freshness-claim',run()['compositorFreshnessVerified'] is False)
    check('due',run(now={'realtime_ns':realtime+11_000_000_000,'boottime_ns':boottime+11_000_000_000})['decision']=='OFFLINE_RECOVERY_DUE')
    check('new-boot',run(b='b'*36)['decision']=='BLOCK_NEW_BOOT')
    check('clock-divergence',run(now={'realtime_ns':realtime+5_000_000_000,'boottime_ns':boottime})['decision']=='BLOCK_CLOCK_DIVERGENCE')
    for field,value in [('target','eDP-2'),('alternate','eDP-1'),('protected','HDMI-A-2'),('luaSha256','bad'),('configurationAdapter','other'),('original','other'),('candidate','other'),('decision','BLOCK_PLAN'),('physicalExecutionEnabled',True),('executionAuthorized',True),('commandsExecuted',1)]:
        p=dict(plan);p[field]=value
        check('reject-plan-'+field,not run(p=p)['decision'].startswith('OFFLINE_'))
    for field,value in [('pid',0),('starttime',0),('instance',''),('connector','eDP-2')]:
        c=dict(comp);c[field]=value
        check('reject-compositor-'+field,not run(c=c)['decision'].startswith('OFFLINE_'))
    check('invalid-clock-type',run(now={'realtime_ns':True,'boottime_ns':boottime})['decision']=='BLOCK_CLOCK_SAMPLE')
    check('invalid-clock-shape',run(now={'realtime_ns':realtime})['decision']=='BLOCK_CLOCK_SAMPLE')
    side['transaction_id']='b'*64;write();check('foreign-transaction',run()['decision']=='BLOCK_TRANSACTION_BINDING')
    side['transaction_id']=record['id'];side['generation_at_binding']=5;write();check('future-generation',run()['decision']=='BLOCK_TRANSACTION_BINDING')
    side['generation_at_binding']=4;record['state']='UNCERTAIN';write();check('uncertain',run()['decision']=='BLOCK_RECONCILIATION')
    for state in ('CONFIRMED','ROLLED_BACK'):
        record['state']=state;write();check('terminal-'+state,run()['decision']=='OFFLINE_TERMINAL')
    record['state']='APPLIED';write()
    before=(root/'recovery.json').read_bytes();m.snapshot(root,plan,comp,{'realtime_ns':realtime,'boottime_ns':boottime},boot)
    check('no-record-write',(root/'recovery.json').read_bytes()==before)
print(json.dumps({'selftest':'passed','totalScenarios':len(checks),'results':checks,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
