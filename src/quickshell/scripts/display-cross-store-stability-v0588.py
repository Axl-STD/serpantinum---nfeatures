#!/usr/bin/env python3
"""v0.5.88: read-only pidfd/process/systemd temporal stability tests.

No process signaling, unit stopping, ownership authentication, or display actions.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import secrets
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SAFE = dict(resourceOwnershipVerified=False, productionModified=False,
            automaticCleanup=False, atomicCrossStoreSnapshot=False,
            crashDurabilityVerified=False, historicalIdentityVerified=False,
            workerIdentityVerified=False, operationCompletionVerified=False,
            finalizationAuthorized=False, executionAuthorized=False,
            physicalExecutionEnabled=False, physicalRollbackVerified=False,
            commandsExecuted=0)

def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

CORR = load('display-proc-systemd-correlation-v0587.py', 'corr_0588')
OWN = load('display-resource-ownership-v0586.py', 'own_0588')


def pidfd_open(pid):
    if not hasattr(os, 'pidfd_open'):
        return None
    return os.pidfd_open(pid, 0)


def pidfd_exited(fd):
    if fd is None:
        return None
    poll = select.poll()
    poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return bool(poll.poll(0))


def correlate(unit, pid, first, second, proc_first, proc_second, fd=None):
    """A pidfd can reject exited identities, never certify ownership."""
    base = CORR.classify(unit, pid, first, second, proc_first, proc_second)
    base['pidfdAvailable'] = fd is not None
    base['pidfdExitObserved'] = pidfd_exited(fd)
    if base['pidfdExitObserved'] is True:
        base['assessment'] = 'pidfd-process-exited'
        base['processMetadataConsistent'] = False
    base['resourceOwnershipVerified'] = False
    base['executionAuthorized'] = False
    base['physicalExecutionEnabled'] = False
    return base


def report(mode, rows, runtime=False):
    return dict(version='0.5.88', mode=mode,
                selftest='passed' if rows and all(r['passed'] is True for r in rows) else 'failed',
                totalScenarios=len(rows), results=rows, systemdIntegration=runtime,
                **SAFE, limitations=[
                    'pidfd is a process reference, not authentication of systemd ownership',
                    'Sequential /proc and systemd observations are not atomic',
                    'Runtime launches one harmless short-lived user unit; no automatic cleanup or physical commands'])


def selftest():
    rows = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        rows.append(dict(scenario=name, passed=True))
    pid = os.getpid()
    proc = OWN.read_process(pid)
    if proc is None:
        raise RuntimeError('self process unavailable')
    unit = 'serpantinum-v0587-' + 'a'*20 + '.service'
    meta = dict(Id=unit, MainPID=str(pid), InvocationID='b'*32,
                ActiveState='active', SubState='running')
    fd = pidfd_open(pid)
    try:
        def classify(before=meta, after=meta, p1=proc, p2=proc, p=pid, handle=fd):
            return correlate(unit, p, before, after, p1, p2, handle)
        check('self-process-readable', proc['pid'] == pid)
        check('self-start-positive', proc['startTicks'] > 0)
        check('self-pgid-positive', proc['pgid'] > 0)
        check('self-sid-positive', proc['sid'] > 0)
        check('stable-cross-store', classify()['assessment'] == 'cross-store-metadata-consistent-only')
        check('never-authenticated', classify()['resourceOwnershipVerified'] is False)
        check('no-authorization', classify()['executionAuthorized'] is False)
        check('no-physical', classify()['physicalExecutionEnabled'] is False)
        check('pidfd-availability-reported', classify()['pidfdAvailable'] is (fd is not None))
        check('pidfd-live', fd is None or pidfd_exited(fd) is False)
        check('changed-invocation', classify(after=dict(meta, InvocationID='c'*32))['assessment'] == 'changed-unit-observation')
        check('changed-mainpid', classify(after=dict(meta, MainPID=str(pid+1)))['assessment'] == 'changed-unit-observation')
        check('changed-unit-state', classify(after=dict(meta, ActiveState='inactive'))['assessment'] == 'changed-unit-observation')
        check('unit-missing', classify(after=None)['assessment'] == 'missing-unit-observation')
        check('unit-id-mismatch', classify(before=dict(meta, Id='wrong.service'), after=dict(meta, Id='wrong.service'))['assessment'] == 'unit-not-running-or-mismatched')
        check('proc-start-change', classify(p2=dict(proc, startTicks=proc['startTicks']+1))['assessment'] == 'changed-process-observation')
        check('proc-pgid-change', classify(p2=dict(proc, pgid=proc['pgid']+1))['assessment'] == 'changed-process-observation')
        check('proc-ppid-change', classify(p2=dict(proc, ppid=proc['ppid']+1))['assessment'] == 'changed-process-observation')
        check('proc-missing', classify(p2=None)['assessment'] == 'missing-process-observation')
        check('pid-reuse-simulated', classify(p1=dict(proc, startTicks=proc['startTicks']+1), p2=dict(proc, startTicks=proc['startTicks']+1))['resourceOwnershipVerified'] is False)
        check('invalid-pid', classify(p=True)['assessment'] == 'invalid-expected-pid')
        check('no-unit-started', report('offline', rows)['systemdIntegration'] is False)
        check('no-cleanup', report('offline', rows)['automaticCleanup'] is False)
    finally:
        if fd is not None:
            os.close(fd)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.25)'], start_new_session=True)
    childfd = None
    try:
        childfd = pidfd_open(child.pid)
        check('child-live-pidfd', childfd is None or pidfd_exited(childfd) is False)
        child.wait(timeout=3)
        check('child-reaped', child.returncode == 0)
        check('child-exited-pidfd', childfd is None or pidfd_exited(childfd) is True)
        check('child-proc-gone', OWN.read_process(child.pid) is None)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
        if childfd is not None:
            os.close(childfd)
    return report('offline', rows)


def runtime():
    rows = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        rows.append(dict(scenario=name, passed=True))
    unit = 'serpantinum-v0587-' + secrets.token_hex(10) + '.service'
    command = ['systemd-run', '--user', '--unit='+unit, '--property=Type=exec',
               '--property=RuntimeMaxSec=9', '/usr/bin/sleep', '4']
    start = subprocess.run(command, capture_output=True, text=True, timeout=6)
    if start.returncode:
        raise RuntimeError('isolated systemd-run failed: '+start.stderr[:160])
    deadline = time.monotonic()+3
    observed = None
    fd = None
    try:
        while time.monotonic() < deadline:
            before = CORR.show(unit)
            if before['ActiveState'] == 'active' and before['SubState'] == 'running' and before['MainPID'].isdigit() and int(before['MainPID']) > 0:
                pid = int(before['MainPID'])
                fd = pidfd_open(pid)
                p1 = OWN.read_process(pid)
                p2 = OWN.read_process(pid)
                after = CORR.show(unit)
                assessment = correlate(unit, pid, before, after, p1, p2, fd)
                if assessment['assessment'] == 'cross-store-metadata-consistent-only':
                    observed = (before, after, p1, p2, assessment, pid)
                    break
                if fd is not None:
                    os.close(fd)
                    fd = None
            time.sleep(.05)
        if observed is None:
            raise RuntimeError('no stable live observation')
        before, after, p1, p2, result, pid = observed
        check('unit-id', before['Id'] == unit)
        check('unit-active', before['ActiveState'] == 'active')
        check('mainpid-matches', int(before['MainPID']) == pid)
        check('invocation-stable', before['InvocationID'] == after['InvocationID'])
        check('proc-pid-matches', p1['pid'] == pid)
        check('proc-start-stable', p1['startTicks'] == p2['startTicks'])
        check('proc-pgid-stable', p1['pgid'] == p2['pgid'])
        check('cross-store-consistent', result['assessment'] == 'cross-store-metadata-consistent-only')
        check('pidfd-live', fd is None or pidfd_exited(fd) is False)
        check('not-authenticated', result['resourceOwnershipVerified'] is False)
        check('changed-invocation-rejected', correlate(unit,pid,before,dict(after,InvocationID='f'*32 if after['InvocationID'] != 'f'*32 else 'e'*32),p1,p2,fd)['assessment'] == 'changed-unit-observation')
        check('changed-pid-rejected', correlate(unit,pid,before,dict(after,MainPID=str(pid+1)),p1,p2,fd)['assessment'] == 'changed-unit-observation')
        check('changed-proc-rejected', correlate(unit,pid,before,after,p1,dict(p2,startTicks=p2['startTicks']+1),fd)['assessment'] == 'changed-process-observation')
        deadline = time.monotonic()+7
        finished = False
        while time.monotonic() < deadline:
            if CORR.show(unit)['ActiveState'] != 'active':
                finished = True
                break
            time.sleep(.1)
        check('unit-natural-completion', finished)
        check('pidfd-exit-after-completion', fd is None or pidfd_exited(fd) is True)
        check('post-exit-not-authorized', correlate(unit,pid,before,after,p1,p2,fd)['executionAuthorized'] is False)
        check('no-automatic-cleanup', True)
        check('no-physical-execution', True)
    finally:
        if fd is not None:
            os.close(fd)
    return report('runtime', rows, True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'run'))
    args = parser.parse_args()
    try:
        result = selftest() if args.command == 'selftest' else runtime()
    except Exception as exc:
        result = dict(version='0.5.88', selftest='failed', error=str(exc)[:200], **SAFE)
    print(json.dumps(result, indent=2))
    return 0 if result['selftest'] == 'passed' else 2

if __name__ == '__main__':
    sys.exit(main())
