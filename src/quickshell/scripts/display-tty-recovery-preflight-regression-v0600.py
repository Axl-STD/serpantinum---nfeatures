#!/usr/bin/env python3
"""Offline validation of TTY read-only preflight, no compositor needed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import hashlib
import copy
import sys

p=Path(__file__).with_name('display-tty-recovery-preflight-v0600.py')
s=importlib.util.spec_from_file_location('tty_preflight',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
base={'name':'eDP-1','width':1920,'height':1080,'scale':1,'transform':0,'x':0,'y':0,'disabled':False,'refreshRate':60.061,'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']}
hdmi=dict(base,name='HDMI-A-1',x=1920,refreshRate=60.0,availableModes=['1920x1080@60.00Hz'])
instances=[{'instance':'example_123','pid':1234}]
results=[]
def check(name, fn, accepted):
    try:
        result=fn()
        ok=accepted and result['decision']=='READ_ONLY_READY'
    except (m.PreflightDenied, ValueError, TypeError, KeyError):
        ok=not accepted
    results.append({'scenario':name,'passed':ok})
    if not ok: raise AssertionError(name)
def v(inst=None, mon=None, backup=None, socket=True, tty='/dev/tty2', required=True):
    return m.validate(inst if inst is not None else copy.deepcopy(instances),mon if mon is not None else [copy.deepcopy(base),copy.deepcopy(hdmi)],backup if backup is not None else [copy.deepcopy(base),copy.deepcopy(hdmi)],b'config',socket,tty,required)
try:
    check('valid',lambda:v(),True)
    check('graphical-denied',lambda:v(tty='/dev/pts/0'),False)
    check('graphical-explicit-readonly',lambda:v(tty='/dev/pts/0',required=False),True)
    check('socket-missing',lambda:v(socket=False),False)
    check('no-instance',lambda:v(inst=[]),False)
    check('two-instances',lambda:v(inst=instances*2),False)
    check('bad-instance-type',lambda:v(inst=[{'instance':1,'pid':1234}]),False)
    check('bad-instance-path',lambda:v(inst=[{'instance':'../a','pid':1234}]),False)
    check('bad-pid',lambda:v(inst=[{'instance':'abc','pid':0}]),False)
    check('no-monitors',lambda:v(mon=[]),False)
    check('duplicate-monitor',lambda:v(mon=[base,base]),False)
    check('unknown-monitor',lambda:v(mon=[base,dict(hdmi,name='DP-1')]),False)
    check('backup-missing',lambda:v(backup=[]),False)
    check('backup-mismatch',lambda:v(backup=[base,dict(hdmi,name='DP-1')]),False)
    for field,value in [('width',1280),('height',720),('scale',1.25),('transform',1),('x',100),('y',100),('disabled',True),('refreshRate',144.06)]:
        check('edp-'+field,lambda f=field,x=value:v(mon=[dict(base,**{f:x}),hdmi]),False)
    for field,value in [('width',1280),('height',720),('scale',1.25),('transform',1),('x',100),('y',100),('disabled',True),('refreshRate',50.0)]:
        check('hdmi-'+field,lambda f=field,x=value:v(mon=[base,dict(hdmi,**{f:x})]),False)
    check('mode-missing',lambda:v(mon=[dict(base,availableModes=['1920x1080@60.06Hz']),hdmi]),False)
    check('mode-invalid',lambda:v(mon=[dict(base,availableModes='bad'),hdmi]),False)
    check('refresh-tolerance',lambda:v(mon=[dict(base,refreshRate=60.06),hdmi]),True)
    check('invalid-json',lambda:m.load_json('{'),False)
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        f=root/'test';f.write_bytes(b'hello')
        check('safe-file',lambda:{'decision':'READ_ONLY_READY'} if m.safe_file(f)==b'hello' else None,True)
        link=root/'link';link.symlink_to(f)
        check('symlink-denied',lambda:m.safe_file(link),False)
        (root/'huge').write_bytes(b'a'*(1024*1024+1))
        check('oversize-denied',lambda:m.safe_file(root/'huge'),False)
    print(json.dumps({'version':'0.6.0','selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':str(exc),'totalScenarios':len(results),'results':results},indent=2))
    sys.exit(2)
