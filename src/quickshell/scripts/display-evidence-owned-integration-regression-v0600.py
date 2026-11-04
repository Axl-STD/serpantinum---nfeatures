#!/usr/bin/env python3
"""Offline-only integration of Evidence Security owned route and legacy contracts."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
SOURCE=HERE/'display-evidence-security-gate-v0590.py'

def load():
    spec=importlib.util.spec_from_file_location('evidence_owned_test',SOURCE)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def selftest():
    rows=[]
    def check(name,condition):
        rows.append({'scenario':name,'passed':bool(condition)})
        if not condition: raise AssertionError(name)
    mod=load()
    check('owned-api-present',callable(mod.execute_owned_offline))
    check('historical-offline-contract',len(mod.OFFLINE)==2)
    check('historical-runtime-contract',len(mod.RUNTIME)==2)
    with tempfile.TemporaryDirectory(prefix='serpantinum-evidence-owned-') as tmp:
        root=Path(tmp); os.chmod(root,0o700)
        old=os.environ.pop('SERPANTINUM_RUNTIME_ADMISSION_FD',None)
        try:
            payload=mod.execute_owned_offline(root,timeout=100)
            checks={
                'owned-passed':payload.get('selftest')=='passed',
                'owned-mode':payload.get('mode')=='offline-coordinator-owned-evidence',
                'owned-target':payload.get('target')=='combined',
                'owned-evidence-count':payload.get('evidenceScenarios')==2,
                'owned-no-inherited-fd':payload.get('workerLockFdInherited') is False,
                'owned-no-auth-claim':payload.get('authenticatedDelegationImplemented') is False,
                'owned-no-systemd':payload.get('systemdIntegration') is False,
                'owned-no-execution':payload.get('executionAuthorized') is False,
                'owned-no-physical':payload.get('physicalExecutionEnabled') is False,
                'owned-no-commands':type(payload.get('commandsExecuted')) is int and payload['commandsExecuted']==0,
                'owned-limitations':bool(payload.get('limitations')),
                'owner-lock-preserved':(root/('serpantinum-display-gate-'+str(os.getuid())+'.lock')).is_file(),
            }
            for k,v in checks.items():check(k,v)
            check('legacy-offline-target-count',len(mod.OFFLINE)==2)
            check('legacy-runtime-target-count',len(mod.RUNTIME)==2)
            check('legacy-safety-flags', 'physicalExecutionEnabled' in mod.FALSE_FLAGS)
            env=os.environ.copy();env['XDG_RUNTIME_DIR']=str(root)
            env.pop('SERPANTINUM_RUNTIME_ADMISSION_FD',None)
            check('cli-owned-command-available', 'offline-owned' in Path(SOURCE).read_text())
            env['SERPANTINUM_RUNTIME_ADMISSION_FD']='999999'
            bad=subprocess.run([sys.executable,'-B',str(SOURCE),'offline-owned'],env=env,capture_output=True,text=True,timeout=10)
            check('inherited-env-denied',bad.returncode==2)
            check('inherited-env-failclosed',json.loads(bad.stdout).get('executionAuthorized') is False)
            try:mod.execute_owned_offline(root,timeout=0)
            except ValueError: check('invalid-timeout-rejected',True)
            else:check('invalid-timeout-rejected',False)
            check('historical-flags-present', 'executionAuthorized' in mod.FALSE_FLAGS)
        finally:
            if old is not None:os.environ['SERPANTINUM_RUNTIME_ADMISSION_FD']=old
    return {'version':'0.6.0','mode':'offline-evidence-owned-integration','selftest':'passed',
        'totalScenarios':len(rows),'results':rows,'authenticatedDelegationImplemented':False,
        'systemdIntegration':False,'executionAuthorized':False,'physicalExecutionEnabled':False,
        'commandsExecuted':0,'limitations':['No real runtime delegation or display changes']}

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('selftest',));p.parse_args()
    try: result=selftest()
    except BaseException as exc:
        result={'version':'0.6.0','mode':'offline-evidence-owned-integration','selftest':'failed',
            'error':type(exc).__name__+': '+str(exc)[:160],'executionAuthorized':False,
            'physicalExecutionEnabled':False,'commandsExecuted':0}
        print(json.dumps(result));return 2
    print(json.dumps(result,indent=2));return 0
if __name__=='__main__':sys.exit(main())
