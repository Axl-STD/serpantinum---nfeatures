#!/usr/bin/env python3
"""v0.5.80.5: fail-closed regression coordinator; runtime requires explicit opt-in.

Only runs the named, source-bound regression scripts. No physical display commands.
"""
import argparse
import contextlib
import fcntl
import os
import signal
import tempfile
import time
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME_LOCK = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / ('serpantinum-display-gate-' + str(os.getuid()) + '.lock')
SUITE_TIMEOUT = 120
GRACE_SECONDS = 2

class GateError(ValueError):
    pass

class GateBusy(GateError):
    pass

class GateInterrupted(GateError):
    pass


@contextlib.contextmanager
def runtime_exclusive():
    """Advisory lock held for the complete runtime gate; no unlink race."""
    folder = RUNTIME_LOCK.parent
    if folder.is_symlink() or not folder.is_dir():
        raise GateError('unsafe runtime lock directory')
    if folder == Path('/tmp'):
        raise GateError('XDG_RUNTIME_DIR required for runtime lock')
    if folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
        raise GateError('unsafe runtime lock directory permissions')
    fd = os.open(RUNTIME_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not __import__('stat').S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
            raise GateError('unsafe runtime lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GateBusy('another runtime gate is active') from exc
        yield
    finally:
        os.close(fd)


def _stop_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def run_isolated(args, timeout=SUITE_TIMEOUT):
    """Run only allowlisted suite argv in a new process group."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, close_fds=True)
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise GateInterrupted('gate interrupted by SIGTERM')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop_group(proc)
        proc.communicate()
        raise GateError('suite timeout') from exc
    except BaseException:
        _stop_group(proc)
        proc.communicate()
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    return subprocess.CompletedProcess(args, proc.returncode, out, err)

OFFLINE = (
    ('guarantee-matrix', 'display-guarantee-contract-matrix-v05801.py', 'verify', 17),
    ('strict-contract', 'display-guarantee-contract-strict-regression-v05804.py', 'selftest', 19),
)
RUNTIME = (
    ('historical-worker', 'display-worker-historical-runtime-v057994.py', 'run', 11),
    ('double-probe', 'display-worker-double-probe-runtime-v057998.py', 'run', 12),
    ('adversarial-double-probe', 'display-worker-adversarial-double-probe-v057999.py', 'run', 7),
)
FALSE_FLAGS = ('executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified',
               'productionModified', 'automaticCleanup', 'historicalIdentityVerified',
               'workerIdentityVerified', 'operationCompletionVerified', 'finalizationAuthorized',
               'atomicCrossStoreSnapshot', 'crashDurabilityVerified')


def validate(label, data, expected, runtime):
    if type(data) is not dict or data.get('selftest') != 'passed':
        raise ValueError(label + ': unsuccessful selftest')
    if type(data.get('totalScenarios')) is not int or data['totalScenarios'] != expected:
        raise ValueError(label + ': invalid scenario count')
    rows = data.get('results')
    if type(rows) is not list or len(rows) != expected or any(type(row) is not dict or row.get('passed') is not True for row in rows):
        raise ValueError(label + ': invalid scenarios')
    if type(data.get('commandsExecuted')) is not int or data['commandsExecuted'] != 0:
        raise ValueError(label + ': commands executed')
    if data.get('systemdIntegration') is not runtime:
        raise ValueError(label + ': incorrect evidence scope')
    for key in FALSE_FLAGS:
        if key in data and data[key] is not False:
            raise ValueError(label + ': unsafe flag ' + key)
    for key in ('executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified',
                'productionModified', 'automaticCleanup'):
        if data.get(key) is not False:
            raise ValueError(label + ': missing critical flag ' + key)
    if runtime and label == 'double-probe' and (type(data.get('historicalProbeCalls')) is not int or data['historicalProbeCalls'] < 2):
        raise ValueError(label + ': insufficient real probes')
    if runtime and label == 'adversarial-double-probe':
        if data.get('priorRuntimeScenariosPassed') != 11 or type(data.get('realHistoricalProbeCalls')) is not int or data['realHistoricalProbeCalls'] < 8:
            raise ValueError(label + ': incomplete adversarial runtime')
    return {'component': label, 'passed': True, 'scenarios': expected,
            'evidenceScope': 'real-systemd-runtime' if runtime else 'offline-selftest-only'}


def invoke(label, filename, mode, expected, runtime, runner=None):
    path = HERE / filename
    if not path.is_file() or path.is_symlink():
        raise ValueError(label + ': missing or unsafe suite')
    if runner is None:
        proc = run_isolated([sys.executable, str(path), mode])
    else:
        proc = runner([sys.executable, str(path), mode], capture_output=True, text=True, timeout=SUITE_TIMEOUT)
    if proc.returncode != 0:
        raise ValueError(label + ': exit=' + str(proc.returncode) + ' ' + proc.stderr[:200])
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(label + ': invalid JSON') from exc
    return validate(label, data, expected, runtime)


def execute(mode, runner=None):
    results = []
    for label, filename, command, expected in OFFLINE:
        results.append(invoke(label, filename, command, expected, False, runner))
    if mode == 'runtime':
        guard = runtime_exclusive() if runner is None else contextlib.nullcontext()
        with guard:
            for label, filename, command, expected in RUNTIME:
                results.append(invoke(label, filename, command, expected, True, runner))
    return results


def selftest():
    cases = []
    def record(label, valid, expected):
        accepted = True
        try:
            validate(label, valid, expected, False)
        except ValueError:
            accepted = False
        if accepted != (label == 'baseline'):
            raise AssertionError(label)
        cases.append({'scenario': label, 'passed': True})
    base = {'selftest': 'passed', 'totalScenarios': 1, 'results': [{'passed': True}],
            'commandsExecuted': 0, 'systemdIntegration': False,
            'productionModified': False, 'automaticCleanup': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False}
    record('baseline', base, 1)
    record('missing-results', {k: v for k, v in base.items() if k != 'results'}, 1)
    record('boolean-count', {**base, 'totalScenarios': True}, 1)
    record('failed-row', {**base, 'results': [{'passed': False}]}, 1)
    record('missing-authorization', {k: v for k, v in base.items() if k != 'executionAuthorized'}, 1)
    record('physical-overclaim', {**base, 'physicalExecutionEnabled': True}, 1)
    record('runtime-overclaim', {**base, 'systemdIntegration': True}, 1)
    record('wrong-count', {**base, 'totalScenarios': 2}, 1)
    record('boolean-commands', {**base, 'commandsExecuted': False}, 1)
    record('atomic-overclaim', {**base, 'atomicCrossStoreSnapshot': True}, 1)
    # Verify the runner is not invoked after the first failing suite.
    calls = []
    class Result:
        returncode = 2
        stdout = '{}'
        stderr = 'injected failure'
    def failed_runner(args, **kwargs):
        calls.append(args)
        return Result()
    try:
        execute('runtime', failed_runner)
    except ValueError:
        pass
    else:
        raise AssertionError('fail-closed')
    if len(calls) != 1 or calls[0][-1] != 'verify':
        raise AssertionError('unexpected continuation')
    cases.append({'scenario': 'stop-on-first-failure', 'passed': True})
    return cases


def report(mode, results):
    return {'version': '0.5.82', 'mode': mode, 'selftest': 'passed',
            'totalScenarios': len(results), 'results': results,
            'systemdIntegration': mode == 'runtime', 'productionModified': False,
            'automaticCleanup': False, 'atomicCrossStoreSnapshot': False,
            'crashDurabilityVerified': False, 'historicalIdentityVerified': False,
            'workerIdentityVerified': False, 'operationCompletionVerified': False,
            'finalizationAuthorized': False, 'executionAuthorized': False,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0,
            'limitations': ['Runtime requires explicit selection and launches isolated user systemd units',
                            'Offline regressions are not physical or runtime proof',
                            'No atomic cross-store snapshot, independent authentication or physical rollback proof']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'offline', 'runtime'))
    args = parser.parse_args()
    try:
        results = selftest() if args.command == 'selftest' else execute(args.command)
        out = report(args.command, results)
        code = 0
    except Exception as exc:
        out = {'version': '0.5.82', 'mode': args.command, 'selftest': 'failed',
               'error': str(exc), 'errorType': type(exc).__name__, 'systemdIntegration': False, 'executionAuthorized': False,
               'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
               'commandsExecuted': 0}
        code = 2
    print(json.dumps(out, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
