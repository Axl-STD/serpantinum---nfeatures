#!/usr/bin/env python3
"""Disposable offline integration tests. No systemd, journal or display operations."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('delegated_runner', HERE / 'display-delegated-evidence-runner-v0600.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    results=[]
    def check(name, condition):
        results.append({'scenario':name,'passed':bool(condition)})
        if not condition:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-delegated-') as temp:
        root=Path(temp)
        runtime=root/'runtime'; broker=root/'broker'
        runtime.mkdir(mode=0o700); broker.mkdir(mode=0o700)
        check('private-runtime',runtime.stat().st_mode & 0o077 == 0)
        check('private-broker',broker.stat().st_mode & 0o077 == 0)
        result=m.run_probe(runtime,broker)
        check('handshake-accepted',result['workerAuthenticatedForHandshake'] is True)
        check('offline-mode',result['mode']=='offline-delegated-evidence')
        check('no-physical-authority',result['physicalExecutionEnabled'] is False)
        check('no-execution-authority',result['executionAuthorized'] is False)
        check('no-commands',result['commandsExecuted']==0)
        check('no-socket-remains',not (broker/'worker.sock').exists())
        check('lock-file-retained',(runtime/m.owner.NAME).is_file())
        check('repeat-independent-probe',m.run_probe(runtime,broker)['selftest']=='passed')
        try: m.run_probe(runtime,broker,'change-monitor')
        except m.DelegationDenied: check('operation-denied',True)
        else: check('operation-denied',False)
        previous=os.environ.get(m.owner.ENV)
        os.environ[m.owner.ENV]='999999'
        try:
            try: m.run_probe(runtime,broker)
            except m.DelegationDenied: check('inherited-env-denied',True)
            else: check('inherited-env-denied',False)
        finally:
            if previous is None: os.environ.pop(m.owner.ENV,None)
            else: os.environ[m.owner.ENV]=previous
        with m.owner.owner_only(runtime):
            try: m.run_probe(runtime,broker)
            except m.owner.AdmissionDenied: check('contention-denied',True)
            else: check('contention-denied',False)
        check('reacquire-after-contention',m.run_probe(runtime,broker)['selftest']=='passed')
        check('worker-no-inherited-fd', 'pass_fds' not in m.run_probe.__code__.co_names)
        check('no-journal-api',not hasattr(m,'advance'))
        check('no-systemd-api',not hasattr(m,'systemd_run'))
        check('no-broker-socket-leak',not (broker/'worker.sock').exists())
    payload={'version':'0.6.0','mode':'offline-delegated-evidence-regression','selftest':'passed',
             'totalScenarios':len(results),'results':results,'authenticatedDelegationImplemented':False,
             'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0,
             'limitations':['Offline fixture integration only; no legacy/global entrypoint modifications',
                            'ACK is not continuing authorization or physical execution permission',
                            'No protection against arbitrary same-UID code or logical rollback']}
    print(json.dumps(payload,indent=2))

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1]!='selftest': raise SystemExit(2)
    main()
