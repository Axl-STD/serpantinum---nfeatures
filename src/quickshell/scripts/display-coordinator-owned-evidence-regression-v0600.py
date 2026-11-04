#!/usr/bin/env python3
"""Offline integration regression using actual legacy/global/combined entrypoints."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SRC = HERE / 'display-coordinator-owned-evidence-v0600.py'
spec = importlib.util.spec_from_file_location('coord', SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    results=[]
    def check(name, ok):
        results.append({'name':name,'passed':bool(ok)})
        if not ok:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-coord-v0600-') as tmp:
        root=Path(tmp)
        os.chmod(root,0o700)
        old=os.environ.pop(m.ENV,None)
        try:
            check('private-root', root.stat().st_mode & 0o077 == 0)
            for target in ('legacy','combined','global'):
                data=m.run(root,target,timeout=60)
                for field,value in [('selftest','passed'),('target',target),('mode','offline-coordinator-owned-evidence'),('workerLockFdInherited',False),('authenticatedDelegationImplemented',False),('systemdIntegration',False),('executionAuthorized',False),('physicalExecutionEnabled',False),('commandsExecuted',0)]:
                    check(target+'-'+field,data[field] == value)
                check(target+'-lock-retained', any(root.glob('serpantinum-display-gate-*.lock')))
            for value in ('invalid','',None):
                try: m.run(root,value)
                except m.CoordinatorDenied: check('invalid-target-'+repr(value),True)
                else: check('invalid-target-'+repr(value),False)
            for timeout in (0,-1,121,'3',True):
                try: m.run(root,'global',timeout)
                except m.CoordinatorDenied: check('invalid-timeout-'+repr(timeout),True)
                else: check('invalid-timeout-'+repr(timeout),False)
            os.environ[m.ENV]='999999'
            try: m.run(root)
            except m.CoordinatorDenied: check('inherited-env-rejected',True)
            else: check('inherited-env-rejected',False)
            os.environ.pop(m.ENV,None)
            check('no-leaked-admission-env',m.ENV not in os.environ)
            check('allowlist-exact',set(m.TARGETS)=={'legacy','combined','global'})
            check('source-no-display-command', all(x not in SRC.read_text() for x in ('hyprctl ', 'wlr-randr ', 'xrandr ')))
            check('no-systemd-run', 'systemd-run' not in SRC.read_text())
        finally:
            if old is not None: os.environ[m.ENV]=old
    payload={'version':'0.6.0','mode':'offline-coordinator-integration-regression','selftest':'passed',
        'totalScenarios':len(results),'results':results,'authenticatedDelegationImplemented':False,
        'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,
        'limitations':['Only offline evidence entrypoints; no runtime delegation authentication']}
    print(json.dumps(payload,indent=2))

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1]!='selftest': raise SystemExit('usage: selftest')
    main()
