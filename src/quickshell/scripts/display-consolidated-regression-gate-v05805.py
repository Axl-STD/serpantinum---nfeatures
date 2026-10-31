#!/usr/bin/env python3
"""v0.5.80.5: fail-closed regression coordinator; runtime requires explicit opt-in.

Only runs the named, source-bound regression scripts. No physical display commands.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
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


def invoke(label, filename, mode, expected, runtime, runner=subprocess.run):
    path = HERE / filename
    if not path.is_file() or path.is_symlink():
        raise ValueError(label + ': missing or unsafe suite')
    proc = runner([sys.executable, str(path), mode], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise ValueError(label + ': exit=' + str(proc.returncode) + ' ' + proc.stderr[:200])
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(label + ': invalid JSON') from exc
    return validate(label, data, expected, runtime)


def execute(mode, runner=subprocess.run):
    results = []
    for label, filename, command, expected in OFFLINE:
        results.append(invoke(label, filename, command, expected, False, runner))
    if mode == 'runtime':
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
    return {'version': '0.5.80.5', 'mode': mode, 'selftest': 'passed',
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
        out = {'version': '0.5.80.5', 'mode': args.command, 'selftest': 'failed',
               'error': str(exc), 'executionAuthorized': False,
               'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
               'commandsExecuted': 0}
        code = 2
    print(json.dumps(out, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
