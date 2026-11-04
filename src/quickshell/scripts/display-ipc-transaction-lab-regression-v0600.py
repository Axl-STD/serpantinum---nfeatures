#!/usr/bin/env python3
"""Offline regression of IPC lab; no Hyprland subprocesses or monitor changes."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ipc_lab',HERE/'display-ipc-transaction-lab-v0600.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]
def check(name,fn):
    try: fn();results.append({'scenario':name,'passed':True})
    except Exception as e: results.append({'scenario':name,'passed':False,'error':str(e)})
def deny(fn):
    try: fn()
    except (m.Denied, OSError, ValueError): return
    raise AssertionError('expected denial')
def plan(target='eDP-1',topology='internal-only'):
    return {'decision':'DYNAMIC_TOPOLOGY_COMMANDS_READONLY','target':target,'topology':topology,'protected':'HDMI-A-1',
      'candidateArgs':['hyprctl','-i','fake-instance','keyword','monitor',f'{target},1920x1080@144.06,0x0,1'],
      'rollbackArgs':['hyprctl','-i','fake-instance','keyword','monitor',f'{target},1920x1080@60.06,0x0,1'],
      'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
def scenario(target='eDP-1',topology='internal-only'):
    tmp=tempfile.TemporaryDirectory(prefix='serpantinum-ipc-lab-')
    root=Path(tmp.name);root.chmod(0o700)
    now=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    r=m.prepare(root,plan(target,topology),now+10_000_000_000,'test-boot')
    return tmp,root,r,now
for target in ('eDP-1','eDP-2'):
  for topology in ('internal-only','internal-and-hdmi'):
    def normal(target=target,topology=topology):
        tmp,root,r,now=scenario(target,topology)
        try:
            a=m.action(root,r['id'],'arm',now_ns=now)
            b=m.action(root,r['id'],'apply',now_ns=now)
            assert b['state']=='APPLIED' and b['generation']==4
            assert m.action(root,r['id'],'inspect')['observed']['mode']==m.TARGET
            c=m.action(root,r['id'],'expire',now_ns=now+11_000_000_000)
            assert c['state']=='ROLLBACK_REQUIRED'
            d=m.action(root,r['id'],'rollback')
            assert d['state']=='ROLLED_BACK' and d['phase']=='COMPLETED'
            assert m.action(root,r['id'],'inspect')['observed']['mode']==m.BASE
            deny(lambda:m.action(root,r['id'],'rollback'))
        finally: tmp.cleanup()
    check('normal-'+target+'-'+topology,normal)
for phase,operation in [('apply','apply'),('rollback','rollback')]:
  for point in ('after-intent','after-effect'):
    def crash(phase=phase,operation=operation,point=point):
        tmp,root,r,now=scenario()
        try:
            m.action(root,r['id'],'arm',now_ns=now)
            if phase=='rollback':m.action(root,r['id'],'apply',now_ns=now)
            q=m.action(root,r['id'],operation,now_ns=now,failpoint=point)
            assert q['state']=='UNCERTAIN'
            assert q['phase']==('APPLY_INTENT' if phase=='apply' else 'ROLLBACK_INTENT')
            deny(lambda:m.action(root,r['id'],operation,now_ns=now))
        finally:tmp.cleanup()
    check('interrupted-'+phase+'-'+point,crash)
for field,value in [('target','HDMI-A-1'),('protected','eDP-1'),('topology','bad'),('decision','READY'),('executionAuthorized',True),('physicalExecutionEnabled',True),('commandsExecuted',1)]:
    def bad(field=field,value=value):
        p=plan();p[field]=value
        deny(lambda:m.validate_plan(p))
    check('reject-'+field,bad)
for key in ('candidateArgs','rollbackArgs'):
    def badcmd(key=key):
        p=plan();p[key]=['hyprctl','keyword','monitor','anything']
        deny(lambda:m.validate_plan(p))
    check('reject-command-'+key,badcmd)
def foreign():
    tmp,root,r,now=scenario()
    try:deny(lambda:m.action(root,'foreign','arm',now_ns=now))
    finally:tmp.cleanup()
check('reject-foreign-token',foreign)
def boot():
    tmp,root,r,now=scenario()
    try:deny(lambda:m.action(root,r['id'],'arm',now_ns=now,boot_id='other'))
    finally:tmp.cleanup()
check('reject-other-boot',boot)
def duplicate():
    tmp,root,r,now=scenario()
    try:deny(lambda:m.prepare(root,plan(),now+20_000_000_000,'test-boot'))
    finally:tmp.cleanup()
check('reject-duplicate-transaction',duplicate)
def symlink():
    tmp,root,r,now=scenario()
    try:
        (root/'simulated-ipc.json').unlink();(root/'simulated-ipc.json').symlink_to('/etc/passwd')
        deny(lambda:m.action(root,r['id'],'inspect'))
    finally:tmp.cleanup()
check('reject-symlink-observation',symlink)
def permission():
    tmp,root,r,now=scenario()
    try:
        root.chmod(0o755);deny(lambda:m.action(root,r['id'],'inspect'))
    finally:tmp.cleanup()
check('reject-unsafe-directory',permission)
def baddeadline():
    tmp=tempfile.TemporaryDirectory();root=Path(tmp.name);root.chmod(0o700)
    try:deny(lambda:m.prepare(root,plan(),time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1,'test-boot'))
    finally:tmp.cleanup()
check('reject-expired-deadline',baddeadline)
print(json.dumps({'version':'0.6.0','selftest':'passed' if all(x['passed'] for x in results) else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
raise SystemExit(0 if all(x['passed'] for x in results) else 2)
