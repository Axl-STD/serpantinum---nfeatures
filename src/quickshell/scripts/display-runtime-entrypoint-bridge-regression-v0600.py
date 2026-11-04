#!/usr/bin/env python3
"""Disposable cross-entrypoint lock and journal integration tests; offline only."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent

def load(name, key):
    spec = importlib.util.spec_from_file_location(key, HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

bridge = load('display-runtime-entrypoint-bridge-v0600.py', 'bridge_test')
global_gate = load('display-global-runtime-gate-v0591.py', 'bridge_global')
legacy = load('display-consolidated-regression-gate-v05805.py', 'bridge_legacy')
shared = load('display_runtime_admission_v0592.py', 'bridge_shared')


def selftest():
    rows=[]
    def check(name, result):
        rows.append({'scenario':name,'passed':bool(result)})
        if not result:
            raise AssertionError(name)
    def rejects(fn):
        try:
            fn()
        except (RuntimeError, ValueError, OSError):
            return True
        return False
    with tempfile.TemporaryDirectory(prefix='serpantinum-bridge-') as tmp:
        base=Path(tmp)
        runtime,journal,broker=(base/name for name in ('runtime','journal','broker'))
        for p in (runtime,journal,broker): p.mkdir(mode=0o700)
        previous=os.environ.get('XDG_RUNTIME_DIR')
        previous_fd=os.environ.pop(shared.ENV,None)
        old_legacy=legacy.RUNTIME_LOCK
        os.environ['XDG_RUNTIME_DIR']=str(runtime)
        legacy.RUNTIME_LOCK=runtime/shared.NAME
        try:
            check('shared-name',shared.NAME==bridge.owner.NAME)
            check('global-name',global_gate.LOCK_NAME==shared.NAME)
            check('legacy-name',legacy.RUNTIME_LOCK.name==shared.NAME)
            check('same-runtime-root',legacy.RUNTIME_LOCK.parent==runtime)
            check('no-delegation-env',shared.ENV not in os.environ)
            check('empty-no-proof',bridge.RuntimeWriter(runtime,journal,broker).__class__ is bridge.RuntimeWriter)
            writer=bridge.RuntimeWriter(runtime,journal,broker)
            check('before-enter-denied',rejects(writer.decision))
            with writer:
                check('writer-active',writer.report()['operationalWriter'] is True)
                check('bridge-offline',writer.report()['bridgeScope']=='offline-cooperating-lock-path')
                check('no-physical',writer.report()['physicalExecutionEnabled'] is False)
                check('no-execution',writer.report()['executionAuthorized'] is False)
                check('no-auth-delegation',writer.report()['authenticatedDelegationImplemented'] is False)
                check('no-universal-exclusion',writer.report()['universalRuntimeExclusionVerified'] is False)
                check('zero-commands',writer.report()['commandsExecuted']==0)
                check('empty-journal-not-safe',writer.decision()=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
                check('legacy-contended',rejects(lambda: next_context(legacy.runtime_exclusive())))
                check('global-contended',rejects(lambda: next_context(global_gate.global_lock(runtime))))
                check('shared-contended',rejects(lambda: next_context(shared.admission(runtime))))
                check('second-writer-contended',rejects(lambda: next_context(bridge.RuntimeWriter(runtime,journal,broker))))
                check('no-raw-advance',not hasattr(writer,'advance'))
                check('no-raw-prepare',not hasattr(writer,'prepare'))
                check('no-physical-execute',not hasattr(writer,'execute'))
                check('invalid-pid-denied',rejects(lambda:writer.transition(0,1,'applying')))
                check('no-journal-transition-denied',rejects(lambda:writer.transition(os.getpid(),1,'applying')))
                outcomes=[]
                thread=threading.Thread(target=lambda:outcomes.append(rejects(writer.decision)))
                thread.start();thread.join(3)
                check('foreign-thread-finished',not thread.is_alive())
                check('foreign-thread-denied',outcomes==[True])
                check('runtime-lock-exists',(runtime/shared.NAME).is_file())
                check('journal-lock-exists',(journal/'.intent-serialize.lock').is_file())
                check('broker-socket-exists',(broker/'worker.sock').is_socket())
                check('runtime-lock-private',((runtime/shared.NAME).stat().st_mode & 0o777)==0o600)
                check('journal-lock-private',((journal/'.intent-serialize.lock').stat().st_mode & 0o777)==0o600)
                check('no-delegation-leak',shared.ENV not in os.environ)
            check('broker-socket-removed',not (broker/'worker.sock').exists())
            check('runtime-lock-preserved',(runtime/shared.NAME).is_file())
            check('journal-lock-preserved',(journal/'.intent-serialize.lock').is_file())
            check('bridge-reuse-denied',rejects(lambda:next_context(writer)))
            check('legacy-recovered',can_enter(legacy.runtime_exclusive))
            check('global-recovered',can_enter(lambda:global_gate.global_lock(runtime)))
            check('shared-recovered',can_enter(lambda:shared.admission(runtime)))
            check('bridge-recovered',can_enter(lambda:bridge.RuntimeWriter(runtime,journal,broker)))
            with legacy.runtime_exclusive():
                check('legacy-blocks-bridge',rejects(lambda:next_context(bridge.RuntimeWriter(runtime,journal,broker))))
                check('legacy-blocks-global',rejects(lambda:next_context(global_gate.global_lock(runtime))))
            with global_gate.global_lock(runtime):
                check('global-blocks-bridge',rejects(lambda:next_context(bridge.RuntimeWriter(runtime,journal,broker))))
                check('global-delegation-gap-characterized',not rejects(lambda:next_context(legacy.runtime_exclusive())))
            with shared.admission(runtime):
                check('shared-blocks-bridge',rejects(lambda:next_context(bridge.RuntimeWriter(runtime,journal,broker))))
                check('shared-delegation-gap-characterized',not rejects(lambda:next_context(legacy.runtime_exclusive())))
            check('shared-env-restored',shared.ENV not in os.environ)
            check('no-automatic-journal',not (journal/'intent.json').exists())
            check('no-auto-recovery',can_enter(lambda:bridge.RuntimeWriter(runtime,journal,broker)))
            os.environ[shared.ENV]='999999'
            check('forged-env-denied',rejects(lambda:next_context(bridge.RuntimeWriter(runtime,journal,broker))))
            os.environ.pop(shared.ENV,None)
            check('recovered-after-forgery',can_enter(lambda:bridge.RuntimeWriter(runtime,journal,broker)))
        finally:
            legacy.RUNTIME_LOCK=old_legacy
            if previous is None: os.environ.pop('XDG_RUNTIME_DIR',None)
            else: os.environ['XDG_RUNTIME_DIR']=previous
            if previous_fd is not None: os.environ[shared.ENV]=previous_fd
    return {'version':'0.6.0','mode':'offline-cross-entrypoint-bridge','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'authenticatedDelegationImplemented':False,
            'universalRuntimeExclusionVerified':False,'commandsExecuted':0,
            'limitations':['Cooperating lock-path exclusion only; no live systemd or display calls',
                           'Legacy accepts inherited admission during global/shared owner scope; delegation gap reproduced',
                           'Journal state and physical recovery remain unverified']}


def next_context(context):
    with context:
        return True


def can_enter(factory):
    try:
        with factory():
            return True
    except (RuntimeError, ValueError, OSError):
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(),indent=2))

if __name__=='__main__': main()
