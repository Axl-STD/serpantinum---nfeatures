#!/usr/bin/env python3
"""v0.5.90: combined evidence security gate; read-only checks and opt-in runtime."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
VERSION = '0.5.90'
TIMEOUT = 360
OFFLINE = (
    ('historical-evidence', 'display-consolidated-regression-gate-v05805.py', 'offline', 2),
    ('resource-identity', 'display-identity-evidence-gate-v0589.py', 'offline', 4),
)
RUNTIME = (
    ('historical-evidence', 'display-consolidated-regression-gate-v05805.py', 'runtime', 5),
    ('resource-identity', 'display-identity-evidence-gate-v0589.py', 'runtime', 6),
)
FALSE_FLAGS = ('resourceOwnershipVerified', 'productionModified', 'automaticCleanup',
    'atomicCrossStoreSnapshot', 'crashDurabilityVerified', 'historicalIdentityVerified',
    'workerIdentityVerified', 'operationCompletionVerified', 'finalizationAuthorized',
    'executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified')


def validate(label, payload, expected, runtime):
    if type(payload) is not dict or payload.get('selftest') != 'passed':
        raise ValueError(label + ': failed or malformed report')
    if payload.get('mode') != ('runtime' if runtime else 'offline'):
        raise ValueError(label + ': mode mismatch')
    if type(payload.get('totalScenarios')) is not int or payload['totalScenarios'] != expected:
        raise ValueError(label + ': scenario count mismatch')
    rows = payload.get('results')
    if type(rows) is not list or len(rows) != expected:
        raise ValueError(label + ': missing scenario evidence')
    if any(type(row) is not dict or row.get('passed') is not True or
           type(row.get('component')) is not str or not row['component'] or
           type(row.get('scenarios')) is not int or row['scenarios'] <= 0 or
           row.get('evidenceScope') not in ('offline-selftest-only', 'real-systemd-runtime')
           for row in rows):
        raise ValueError(label + ': invalid scenario evidence')
    if runtime:
        n = 3 if label == 'historical-evidence' else 2
        if [r['evidenceScope'] for r in rows] != ['offline-selftest-only'] * (expected-n) + ['real-systemd-runtime'] * n:
            raise ValueError(label + ': invalid runtime scopes')
    elif any(r['evidenceScope'] != 'offline-selftest-only' for r in rows):
        raise ValueError(label + ': offline scope mismatch')
    if payload.get('systemdIntegration') is not runtime:
        raise ValueError(label + ': integration overclaim')
    if type(payload.get('commandsExecuted')) is not int or payload['commandsExecuted'] != 0:
        raise ValueError(label + ': physical commands contract')
    for key in FALSE_FLAGS:
        if key in payload and payload[key] is not False:
            raise ValueError(label + ': unsafe flag ' + key)
    for key in ('productionModified','automaticCleanup','executionAuthorized',
                'physicalExecutionEnabled','physicalRollbackVerified','atomicCrossStoreSnapshot'):
        if payload.get(key) is not False:
            raise ValueError(label + ': missing safety flag ' + key)
    if label == 'resource-identity' and payload.get('resourceOwnershipVerified') is not False:
        raise ValueError(label + ': ownership not explicitly unverified')
    return {'component': label, 'passed': True, 'scenarios': expected,
            'evidenceScope': 'mixed-offline-and-real-systemd-runtime' if runtime else 'offline-selftest-only',
            'subcomponents': rows}


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


def runner(argv):
    from display_runtime_admission_v0592 import child_fds
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, close_fds=True,
                            pass_fds=child_fds())
    try:
        out, err = proc.communicate(timeout=TIMEOUT)
    except BaseException:
        stop_group(proc)
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def invoke(entry, runtime, run=runner):
    label, filename, command, count = entry
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise ValueError(label + ': missing or unsafe suite')
    completed = run([sys.executable, str(path), command])
    if completed.returncode != 0:
        raise ValueError(label + ': suite returned nonzero')
    try:
        data = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(label + ': invalid JSON') from exc
    return validate(label, data, count, runtime)


def execute(mode, run=runner):
    if mode not in ('offline','runtime'):
        raise ValueError('invalid mode')
    from display_runtime_admission_v0592 import admission
    guard = admission() if mode == 'runtime' and run is runner else contextlib.nullcontext()
    with guard:
        results = [invoke(entry, False, run) for entry in OFFLINE]
        if mode == 'runtime':
            results.extend(invoke(entry, True, run) for entry in RUNTIME)
    return results


def report(mode, results):
    return {'version': VERSION, 'mode': mode, 'selftest': 'passed',
            'totalScenarios': len(results), 'results': results,
            'systemdIntegration': mode == 'runtime',
            **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0,
            'limitations': ['Shared runtime admission applies to cooperating v0.5.92 entrypoints',
                'Sequential observations are not an atomic cross-store snapshot or authenticated ownership',
                'Runtime is opt-in; no physical display commands or automatic cleanup']}



def execute_owned_offline(runtime_root, timeout=60):
    """Opt-in offline evidence with owner-only admission; no inherited lock FD."""
    import importlib.util
    source = HERE / 'display-coordinator-owned-evidence-v0600.py'
    if source.is_symlink() or not source.is_file():
        raise ValueError('unsafe owned coordinator dependency')
    spec = importlib.util.spec_from_file_location('display_owned_combined_v0600', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        result = module.run(runtime_root, 'combined', timeout=timeout)
    except module.CoordinatorDenied as exc:
        raise ValueError('owned combined evidence rejected') from exc
    if (type(result) is not dict or result.get('selftest') != 'passed' or
            result.get('mode') != 'offline-coordinator-owned-evidence' or
            result.get('target') != 'combined' or
            result.get('evidenceScenarios') != 2 or
            result.get('workerLockFdInherited') is not False or
            result.get('systemdIntegration') is not False or
            result.get('authenticatedDelegationImplemented') is not False or
            result.get('executionAuthorized') is not False or
            result.get('physicalExecutionEnabled') is not False or
            type(result.get('commandsExecuted')) is not int or
            result['commandsExecuted'] != 0):
        raise ValueError('unsafe owned combined evidence report')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('offline','runtime','offline-owned'))
    mode = parser.parse_args().command
    if mode == 'offline-owned':
        try:
            result = execute_owned_offline(os.environ.get('XDG_RUNTIME_DIR', ''))
        except BaseException as exc:
            print(json.dumps({'version':'0.6.0','mode':'offline-owned',
                'selftest':'failed','error':type(exc).__name__,
                'executionAuthorized':False,'physicalExecutionEnabled':False,
                'commandsExecuted':0}))
            return 2
        print(json.dumps(result, indent=2))
        return 0
    try:
        result = report(mode, execute(mode))
    except BaseException as exc:
        print(json.dumps({'version': VERSION, 'mode': mode, 'selftest': 'failed',
                          'error': type(exc).__name__ + ': ' + str(exc)[:180],
                          **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
