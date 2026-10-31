#!/usr/bin/env python3
"""v0.5.89: negative, ordering, fail-closed and isolation tests for identity gate."""
import argparse
import contextlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('identity_gate_0589', HERE / 'display-identity-evidence-gate-v0589.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def selftest():
    rows = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        rows.append({'scenario': name, 'passed': True})
    def payload(n=1, runtime=False):
        return {'selftest': 'passed', 'mode': 'runtime' if runtime else 'offline',
                'totalScenarios': n, 'results': [{'scenario': 'case-' + str(i), 'passed': True} for i in range(n)],
                'systemdIntegration': runtime, 'commandsExecuted': 0,
                **{k: False for k in gate.FALSE_FLAGS}}
    base = payload()
    def accepted(data, count=1, runtime=False):
        try:
            gate.validate('fixture', data, count, runtime)
            return True
        except ValueError:
            return False
    check('valid-offline', accepted(base))
    check('valid-runtime', accepted(payload(runtime=True), runtime=True))
    mutations = [
        ('missing-results', {k:v for k,v in base.items() if k != 'results'}),
        ('empty-results', {**base, 'results': []}),
        ('boolean-count', {**base, 'totalScenarios': True}),
        ('float-count', {**base, 'totalScenarios': 1.0}),
        ('wrong-count', {**base, 'totalScenarios': 2}),
        ('failed-row', {**base, 'results': [{'scenario':'x','passed':False}]}),
        ('nonboolean-passed', {**base, 'results': [{'scenario':'x','passed':1}]}),
        ('duplicate-scenarios', {**payload(2), 'results': [{'scenario':'x','passed':True}]*2}),
        ('missing-scenario', {**base, 'results': [{'passed':True}]}),
        ('non-dict-row', {**base, 'results': [True]}),
        ('missing-ownership', {k:v for k,v in base.items() if k != 'resourceOwnershipVerified'}),
        ('ownership-overclaim', {**base, 'resourceOwnershipVerified':True}),
        ('physical-overclaim', {**base, 'physicalExecutionEnabled':True}),
        ('cleanup-overclaim', {**base, 'automaticCleanup':True}),
        ('atomic-overclaim', {**base, 'atomicCrossStoreSnapshot':True}),
        ('boolean-commands', {**base, 'commandsExecuted':False}),
        ('nonzero-commands', {**base, 'commandsExecuted':1}),
        ('scope-overclaim', {**base, 'systemdIntegration':True}),
        ('runtime-mode-overclaim', {**base, 'mode':'runtime'}),
        ('failed-selftest', {**base, 'selftest':'failed'}),
    ]
    for name, data in mutations:
        check(name, not accepted(data, 2 if name == 'duplicate-scenarios' else 1))
    calls = []
    def fake_runner(argv):
        calls.append((Path(argv[1]).name, argv[2]))
        name = Path(argv[1]).name
        entry = next(e for e in gate.OFFLINE + gate.RUNTIME if e[1] == name and e[2] == argv[2])
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload(entry[3], argv[2] == 'run')), '')
    check('offline-four-suites', len(gate.execute('offline', fake_runner)) == 4)
    check('offline-order', calls == [(e[1],e[2]) for e in gate.OFFLINE])
    check('offline-never-runtime', all(mode == 'selftest' for _, mode in calls))
    calls.clear()
    locks = []
    @contextlib.contextmanager
    def fake_lock():
        locks.append('enter')
        yield
        locks.append('exit')
    check('runtime-six-suites', len(gate.execute('runtime', fake_runner, fake_lock)) == 6)
    check('runtime-order', calls == [(e[1],e[2]) for e in gate.OFFLINE + gate.RUNTIME])
    check('runtime-lock-held', locks == ['enter','exit'])
    calls.clear()
    def broken_runner(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 3, '{}', 'failed')
    try:
        gate.execute('runtime', broken_runner, fake_lock)
        stopped = False
    except ValueError:
        stopped = True
    check('stop-first-failure', stopped and len(calls) == 1)
    check('runtime-lock-not-entered-on-offline-failure', locks == ['enter','exit'])
    check('report-no-authentication', gate.report('runtime', [])['resourceOwnershipVerified'] is False)
    check('report-no-physical', gate.report('runtime', [])['commandsExecuted'] == 0)
    check('runtime-opt-in', gate.report('offline', [])['systemdIntegration'] is False)
    return gate.report('selftest', rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    try:
        result = selftest()
    except Exception as exc:
        print(json.dumps({'selftest':'failed','error':str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
