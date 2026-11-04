#!/usr/bin/env python3
"""Offline contract regression, with no Hyprland calls."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import os
import socket

p=Path(__file__).with_name('display-physical-supervisor-contract-v0600.py')
spec=importlib.util.spec_from_file_location('sup',p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
results=[]
def check(name, condition):
    results.append({'scenario':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)
def denied(name,fn):
    try: fn()
    except m.SupervisorDenied: check(name,True)
    else: check(name,False)
try:
    with tempfile.TemporaryDirectory() as td:
        runtime=Path(td)
        folder=runtime/'hypr'/'test-instance'
        folder.mkdir(parents=True,mode=0o700)
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        sock.bind(str(folder/'.socket.sock'))
        instances=[{'instance':'test-instance','pid':os.getpid()}]
        gate={'decision':'DRY_RUN_READY','physicalExecutionEnabled':False,'commandsExecuted':0}
        check('valid-identity',m.check_instance(instances,runtime,os.getuid())==('test-instance',os.getpid()))
        check('valid-gate',m.validate_readonly(gate,instances,runtime,os.getuid())['decision']=='SUPERVISOR_READONLY_READY')
        for name,field,value in [('reject-gate','decision','BLOCK_AND_INVESTIGATE'),('reject-physical','physicalExecutionEnabled',True),('reject-commands','commandsExecuted',1)]:
            denied(name,lambda field=field,value=value:m.validate_readonly(dict(gate,**{field:value}),instances,runtime,os.getuid()))
        for name,items in [('empty',[]),('multiple',instances*2),('non-list',{}),('bad-record',[1]),('bad-pid',[dict(instances[0],pid='x')]),('pid-zero',[dict(instances[0],pid=0)]),('bad-name',[dict(instances[0],instance='../bad')]),('missing-name',[dict(instances[0],instance='missing')]),('unknown-pid',[dict(instances[0],pid=999999999)])]:
            denied(name,lambda items=items:m.check_instance(items,runtime,os.getuid()))
        sock.close()
        (folder/'.socket.sock').unlink()
        denied('missing-socket',lambda:m.check_instance(instances,runtime,os.getuid()))
        (folder/'.socket.sock').symlink_to('/dev/null')
        denied('symlink-socket',lambda:m.check_instance(instances,runtime,os.getuid()))
        (folder/'.socket.sock').unlink()
        folder.chmod(0o755)
        denied('unsafe-directory-mode',lambda:m.check_instance(instances,runtime,os.getuid()))
    source=p.read_text()
    for word in ('hyprctl keyword','hyprctl dispatch','os.system(', 'shell=True', 'monitors.conf.write'):
        check('no-write-api-'+word,word not in source)
    print(json.dumps({'version':'0.6.0','mode':'offline-physical-supervisor-contract','selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
except BaseException as exc:
    print(json.dumps({'selftest':'failed','error':str(exc),'totalScenarios':len(results),'results':results},indent=2))
    sys.exit(2)
