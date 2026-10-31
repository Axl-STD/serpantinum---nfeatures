#!/usr/bin/env python3
"""v0.5.91: opt-in top-level global runtime admission for combined evidence gate.

Global exclusivity applies to invocations through THIS entrypoint. Legacy direct
entrypoints still use their own existing runtime lock; this is not a universal
lock across unrelated entrypoints. No display operations are performed here.
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
LOCK_NAME = 'serpantinum-display-global-evidence-' + str(os.getuid()) + '.lock'
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
    root = Path(runtime_dir if runtime_dir is not None else os.environ.get('XDG_RUNTIME_DIR', ''))
    if not str(root) or str(root) == '.' or root == Path('/tmp') or root.is_symlink():
        raise GateError('private XDG_RUNTIME_DIR required')
    st = root.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise GateError('unsafe runtime directory')
    fd = os.open(root / LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
            raise GateError('unsafe lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GateError('global evidence gate busy') from exc
        yield
    finally:
        os.close(fd)

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
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, close_fds=True)
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
    # The global admission lock is DISTINCT from the inner legacy runtime lock.
    # Child closes inherited descriptors; therefore no nested self-deadlock.
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
            'globalAdmissionScope': 'v0591-entrypoint-only',
            'limitations': ['Global admission covers only callers using this v0.5.91 entrypoint',
                            'Legacy direct gates retain their own runtime locks',
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
