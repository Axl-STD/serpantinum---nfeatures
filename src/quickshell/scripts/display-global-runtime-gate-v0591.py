#!/usr/bin/env python3
"""v0.5.91: opt-in top-level global runtime admission for combined evidence gate.

Shared admission applies to cooperating updated runtime entrypoints.
No independent ownership authentication is implied. No display operations are performed here.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TARGET = HERE / 'display-evidence-security-gate-v0590.py'
LOCK_NAME = 'serpantinum-display-gate-' + str(os.getuid()) + '.lock'
TIMEOUT = 600
FALSE_FLAGS = ('resourceOwnershipVerified', 'productionModified', 'automaticCleanup',
               'atomicCrossStoreSnapshot', 'crashDurabilityVerified',
               'historicalIdentityVerified', 'workerIdentityVerified',
               'operationCompletionVerified', 'finalizationAuthorized',
               'executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified')

class GateError(RuntimeError):
    pass

@contextlib.contextmanager
def global_lock(runtime_dir=None):
    from display_runtime_admission_v0592 import admission
    try:
        with admission(runtime_dir):
            yield
    except RuntimeError as exc:
        raise GateError(str(exc)) from exc


def stop_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()

def isolated_run(argv, timeout=TIMEOUT):
    from display_runtime_admission_v0592 import child_fds
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, close_fds=True,
                            pass_fds=child_fds())
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:
        stop_group(proc)
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)

def validate(payload, mode):
    expected = 4 if mode == 'runtime' else 2
    if type(payload) is not dict or payload.get('selftest') != 'passed' or payload.get('mode') != mode:
        raise GateError('invalid gate result')
    rows = payload.get('results')
    if type(payload.get('totalScenarios')) is not int or payload['totalScenarios'] != expected or type(rows) is not list or len(rows) != expected:
        raise GateError('invalid gate scenario count')
    if any(type(row) is not dict or row.get('passed') is not True for row in rows):
        raise GateError('invalid gate scenario evidence')
    if payload.get('systemdIntegration') is not (mode == 'runtime'):
        raise GateError('incorrect systemd evidence scope')
    if type(payload.get('commandsExecuted')) is not int or payload['commandsExecuted'] != 0:
        raise GateError('physical command claim')
    if any(payload.get(k) is not False for k in FALSE_FLAGS):
        raise GateError('missing or unsafe guarantee')
    return payload

def execute(mode, run=isolated_run, lock_factory=global_lock):
    if mode not in ('offline', 'runtime'):
        raise GateError('invalid mode')
    if TARGET.is_symlink() or not TARGET.is_file():
        raise GateError('missing or unsafe consolidated gate')
    # A verified inherited descriptor delegates the same lock to child gates.
    # Only the known coordinator child inherits it via pass_fds.
    guard = lock_factory() if mode == 'runtime' else contextlib.nullcontext()
    with guard:
        result = run([sys.executable, str(TARGET), mode])
        if result.returncode != 0:
            raise GateError('consolidated gate returned nonzero')
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise GateError('invalid JSON') from exc
        return validate(payload, mode)

def report(mode, payload):
    return {'version': '0.5.91', 'mode': mode, 'selftest': 'passed',
            'totalScenarios': payload['totalScenarios'], 'results': payload['results'],
            'systemdIntegration': mode == 'runtime',
            **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0,
            'globalAdmissionScope': 'v0592-cooperating-entrypoints',
            'limitations': ['Admission covers cooperating updated entrypoints, not arbitrary external callers',
                            'Legacy direct gates use the same admission descriptor under v0.5.92',
                            'No authenticated ownership, atomic cross-store snapshot or physical rollback']}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('offline', 'runtime'))
    mode = parser.parse_args().command
    try:
        payload = report(mode, execute(mode))
    except BaseException as exc:
        payload = {'version': '0.5.91', 'mode': mode, 'selftest': 'failed',
                   'error': type(exc).__name__ + ': ' + str(exc)[:180],
                   **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0}
        print(json.dumps(payload))
        return 2
    print(json.dumps(payload, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
