#!/usr/bin/env python3
"""Offline-only coordinator/worker/journal integration regression."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('session_v0600', HERE / 'display-authority-session-v0600.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def selftest():
    rows = []
    def check(name, condition):
        rows.append({'scenario': name, 'passed': bool(condition), 'classification': 'PASS'})
        if not condition:
            raise AssertionError(name)
    def reject(name, func):
        try:
            func()
        except (RuntimeError, OSError, ValueError):
            check(name, True)
        else:
            check(name, False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-session-') as base:
        base = Path(base)
        roots = [base / n for n in ('runtime','journal','broker')]
        for p in roots:
            p.mkdir(mode=0o700)
        session = m.Session(*roots)
        check('inactive-default', not session.active)
        check('inactive-decision-blocks', session.decision() == 'BLOCK_AND_INVESTIGATE')
        check('inactive-report-no-execution', session.report()['executionAuthorized'] is False)
        reject('inactive-register-rejected', lambda: session.register(os.getpid()))
        reject('same-roots-rejected', lambda: m.Session(roots[0], roots[0], roots[2]).__enter__())
        with session as active:
            check('session-active', active.active)
            check('broker-socket-present', (roots[2]/'worker.sock').is_socket())
            check('runtime-lock-present', (roots[0]/m.owner.NAME).is_file())
            check('journal-lock-present', (roots[1]/'.intent-serialize.lock').is_file())
            check('empty-journal-not-safe', active.decision() == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
            with open(roots[1]/'intent.json', 'wb') as f:
                f.write(b'{invalid')
            check('corrupt-journal-blocks', active.decision() == 'BLOCK_AND_INVESTIGATE')
            (roots[1]/'intent.json').unlink()
            check('no-ack-initially', not active.acknowledged)
            check('report-no-authentication-claim', active.report()['authenticatedDelegationImplemented'] is False)
            check('report-no-universal-exclusion', active.report()['universalRuntimeExclusionVerified'] is False)
            check('report-no-cleanup', active.report()['cleanupAuthorized'] is False)
            check('report-no-physical', active.report()['physicalExecutionEnabled'] is False)
            check('report-zero-commands', active.report()['commandsExecuted'] == 0)
            reject('duplicate-owner-blocked', lambda: m.owner.owner_only(roots[0]).__enter__())
            reject('duplicate-serialized-blocked', lambda: m.serialized.serialized(roots[1]).__enter__())
            reject('duplicate-broker-blocked', lambda: m.worker.Broker(roots[2]).__enter__())
            reject('unregistered-operation-rejected', lambda: active.register(os.getpid(), 'physical-change'))
            # Real child sends handshake while broker serves from this thread.
            code = ('import importlib.util,sys; p=sys.argv[1]; s=importlib.util.spec_from_file_location("w",p);'
                    'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                    'sys.exit(0 if m.request(sys.argv[2],"offline-probe") else 17)')
            child = subprocess.Popen([sys.executable,'-B','-c',code,str(HERE/'display-worker-authority-v0600.py'),str(roots[2]/'worker.sock')],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                active.register(child.pid)
                check('worker-registered', child.pid in active.broker.allowed)
                result = active.handshake()
                check('worker-accepted', result['accepted'] is True)
                check('worker-peer-pid', result['peerPid'] == child.pid)
                check('worker-operation', result['operation'] == 'offline-probe')
                check('worker-registration-consumed', child.pid not in active.broker.allowed)
                check('ack-tracked', child.pid in active.acknowledged)
                check('ack-not-execution', active.report()['executionAuthorized'] is False)
                check('worker-exits-success', child.wait(timeout=5) == 0)
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=5)
                child.stdout.close(); child.stderr.close()
            check('session-still-active', active.active)
            check('post-handshake-not-safe', active.decision() == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        check('session-closed', not session.active)
        check('ack-revoked-on-exit', not session.acknowledged)
        check('socket-removed', not (roots[2]/'worker.sock').exists())
        check('runtime-lock-file-preserved', (roots[0]/m.owner.NAME).is_file())
        check('journal-lock-file-preserved', (roots[1]/'.intent-serialize.lock').is_file())
        check('closed-session-blocks', session.decision() == 'BLOCK_AND_INVESTIGATE')
        reject('closed-register-rejected', lambda: session.register(os.getpid()))
        with m.owner.owner_only(roots[0]):
            check('owner-reacquired-after-close', True)
        with m.serialized.serialized(roots[1]):
            check('journal-reacquired-after-close', True)
        check('no-execution-after-close', session.report()['executionAuthorized'] is False)
    return {'version':'0.6.0','mode':'offline-bound-authority-session','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'authenticatedDelegationImplemented':False,
            'universalRuntimeExclusionVerified':False,'cleanupAuthorized':False,
            'commandsExecuted':0,'limitations':['ACK is not continuous authorization',
            'Session is cooperative and cannot revoke arbitrary worker CPU execution',
            'No real runtime entrypoint, systemd, or physical display operations']}

if __name__=='__main__':
    argparse.ArgumentParser().add_argument('command',choices=('selftest',))
    if len(sys.argv)!=2 or sys.argv[1]!='selftest':
        sys.exit(2)
    print(json.dumps(selftest(),indent=2))
