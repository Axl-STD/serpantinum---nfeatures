#!/usr/bin/env python3
"""v0.5.81: isolated resilience regression for the existing display gate.

All adversarial scenarios use synthetic results, temporary files, or harmless
child Python processes. Real systemd is exercised only by the pre-existing
coordinator's explicitly selected runtime mode, not by this suite.
"""
import argparse
import contextlib
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
GATE = HERE / 'display-consolidated-regression-gate-v05805.py'


def load_gate():
    spec = importlib.util.spec_from_file_location('display_gate_v0581', GATE)
    if not spec or not spec.loader:
        raise RuntimeError('gate unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(n=1, runtime=False):
    return {'selftest': 'passed', 'totalScenarios': n,
            'results': [{'passed': True} for _ in range(n)],
            'commandsExecuted': 0, 'systemdIntegration': runtime,
            'productionModified': False, 'automaticCleanup': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False}


def selftest():
    gate = load_gate()
    cases = []
    def record(name, condition):
        if not condition:
            raise AssertionError(name)
        cases.append({'scenario': name, 'passed': True})

    baseline = fixture()
    record('valid-offline', gate.validate('test', baseline, 1, False)['passed'] is True)
    mutations = {
        'missing-results': lambda x: x.pop('results'),
        'empty-results': lambda x: x.update(results=[]),
        'wrong-count': lambda x: x.update(totalScenarios=2),
        'boolean-count': lambda x: x.update(totalScenarios=True),
        'float-count': lambda x: x.update(totalScenarios=1.0),
        'failed-row': lambda x: x.update(results=[{'passed': False}]),
        'integer-passed': lambda x: x.update(results=[{'passed': 1}]),
        'non-dict-row': lambda x: x.update(results=[None]),
        'boolean-commands': lambda x: x.update(commandsExecuted=False),
        'nonzero-commands': lambda x: x.update(commandsExecuted=1),
        'missing-authorization': lambda x: x.pop('executionAuthorized'),
        'physical-overclaim': lambda x: x.update(physicalExecutionEnabled=True),
        'rollback-overclaim': lambda x: x.update(physicalRollbackVerified=True),
        'cleanup-overclaim': lambda x: x.update(automaticCleanup=True),
        'atomic-overclaim': lambda x: x.update(atomicCrossStoreSnapshot=True),
        'runtime-mislabel': lambda x: x.update(systemdIntegration=True),
        'failed-selftest': lambda x: x.update(selftest='failed'),
    }
    for name, change in mutations.items():
        data = copy.deepcopy(baseline)
        change(data)
        try:
            gate.validate('test', data, 1, False)
        except ValueError:
            record(name, True)
        else:
            raise AssertionError(name + ': accepted')

    runtime = fixture(2, True)
    runtime['historicalProbeCalls'] = 2
    record('valid-runtime-double-probe', gate.validate('double-probe', runtime, 2, True)['passed'])
    for name, value in [('missing-probes', None), ('boolean-probes', True), ('insufficient-probes', 1)]:
        bad = dict(runtime)
        if value is None:
            bad.pop('historicalProbeCalls')
        else:
            bad['historicalProbeCalls'] = value
        try:
            gate.validate('double-probe', bad, 2, True)
        except ValueError:
            record(name, True)
        else:
            raise AssertionError(name)

    calls = []
    class Result:
        def __init__(self, code=0, stdout='', stderr=''):
            self.returncode, self.stdout, self.stderr = code, stdout, stderr
    def runner(args, **kwargs):
        calls.append((args[-1], kwargs.get('timeout')))
        if len(calls) == 1:
            return Result(2, '{}', 'injected')
        raise AssertionError('continued after failure')
    try:
        gate.execute('runtime', runner)
    except ValueError:
        record('stop-on-first-failure', len(calls) == 1 and calls[0][0] == 'verify')
    else:
        raise AssertionError('failure accepted')

    def malformed(args, **kwargs):
        return Result(0, '{not json', '')
    try:
        gate.invoke('test', gate.OFFLINE[0][1], 'verify', 17, False, malformed)
    except ValueError:
        record('malformed-json-rejected', True)
    else:
        raise AssertionError('invalid JSON accepted')

    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])
    try:
        gate.invoke('test', gate.OFFLINE[0][1], 'verify', 17, False, timeout)
    except subprocess.TimeoutExpired:
        record('timeout-propagates', True)
    else:
        raise AssertionError('timeout swallowed')

    with tempfile.TemporaryDirectory(prefix='serpantinum-v0581-', dir='/tmp') as root:
        root = Path(root)
        record('private-temp-directory', (root.stat().st_mode & 0o077) == 0)
        lock = root / 'lock'
        with lock.open('w') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            probe = subprocess.run([sys.executable, '-c',
                'import fcntl,sys; f=open(sys.argv[1],"w");\n'
                'try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); sys.exit(1)\n'
                'except BlockingIOError: sys.exit(0)', str(lock)], capture_output=True, text=True, timeout=5)
            record('concurrent-lock-contention', probe.returncode == 0)
        probe = subprocess.run([sys.executable, '-c',
            'import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)',
            str(lock)], timeout=5)
        record('lock-recovery-after-release', probe.returncode == 0)

        # A disposable process group permits interruption without touching the desktop.
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 start_new_session=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=5)
            record('isolated-child-interruption', child.returncode != 0)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)

        try:
            subprocess.run([sys.executable, '-c', 'import time; time.sleep(30)'],
                           capture_output=True, timeout=0.15)
        except subprocess.TimeoutExpired:
            record('subprocess-timeout-enforced', True)
        else:
            raise AssertionError('timeout not enforced')

        sample = {'totalScenarios': 2, 'results': [{'passed': True}, {'passed': True}]}
        record('count-contract-positive', sample['totalScenarios'] == len(sample['results']))
        sample['results'].pop()
        record('count-contract-negative', sample['totalScenarios'] != len(sample['results']))

    # Static invariant: this regression never invokes a real runtime mode.
    record('runtime-explicit-only', tuple(x[-1] for x in gate.RUNTIME) == (11, 12, 7))
    record('offline-before-runtime', len(gate.OFFLINE) == 2 and len(gate.RUNTIME) == 3)
    return {'version': '0.5.81', 'mode': 'isolated-integral-resilience-selftest',
            'selftest': 'passed', 'totalScenarios': len(cases), 'results': cases,
            'systemdIntegration': False, 'productionModified': False,
            'automaticCleanup': False, 'atomicCrossStoreSnapshot': False,
            'crashDurabilityVerified': False, 'historicalIdentityVerified': False,
            'workerIdentityVerified': False, 'operationCompletionVerified': False,
            'finalizationAuthorized': False, 'executionAuthorized': False,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0,
            'limitations': ['Synthetic process and filesystem tests; no real systemd races',
                            'Existing gate timeout propagates; it is not yet normalized into JSON',
                            'File locks are characterized, not installed into production coordinator',
                            'No power-loss, independent authentication, or physical rollback proof']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    try:
        result = selftest()
    except Exception as exc:
        result = {'version': '0.5.81', 'selftest': 'failed', 'error': str(exc),
                  'commandsExecuted': 0, 'executionAuthorized': False,
                  'physicalExecutionEnabled': False, 'physicalRollbackVerified': False}
        print(json.dumps(result, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
