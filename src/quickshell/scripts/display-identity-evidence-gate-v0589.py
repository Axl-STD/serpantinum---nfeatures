#!/usr/bin/env python3
"""v0.5.89: conservative identity evidence regression gate; runtime opt-in only."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TIMEOUT = 120
OFFLINE = (
    ('resource-identity', 'display-resource-ownership-v0586.py', 'selftest', 31),
    ('disposable-process', 'display-resource-ownership-runtime-v0586.py', 'selftest', 13),
    ('proc-systemd-correlation', 'display-proc-systemd-correlation-v0587.py', 'selftest', 28),
    ('pidfd-stability', 'display-cross-store-stability-v0588.py', 'selftest', 27),
)
RUNTIME = (
    ('proc-systemd-correlation', 'display-proc-systemd-correlation-v0587.py', 'run', 16),
    ('pidfd-stability', 'display-cross-store-stability-v0588.py', 'run', 18),
)
FALSE_FLAGS = ('resourceOwnershipVerified', 'productionModified', 'automaticCleanup',
               'atomicCrossStoreSnapshot', 'crashDurabilityVerified', 'historicalIdentityVerified',
               'workerIdentityVerified', 'operationCompletionVerified', 'finalizationAuthorized',
               'executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified')


def validate(label, data, count, runtime):
    if type(data) is not dict or data.get('selftest') != 'passed':
        raise ValueError(label + ': selftest failed')
    if type(data.get('totalScenarios')) is not int or data['totalScenarios'] != count:
        raise ValueError(label + ': scenario count invalid')
    rows = data.get('results')
    if type(rows) is not list or len(rows) != count or any(type(r) is not dict or r.get('passed') is not True for r in rows):
        raise ValueError(label + ': scenario evidence invalid')
    names = [r.get('scenario') for r in rows]
    if any(type(n) is not str or not n or len(n) > 120 for n in names) or len(set(names)) != len(names):
        raise ValueError(label + ': scenario identities invalid')
    if data.get('systemdIntegration') is not runtime:
        raise ValueError(label + ': evidence scope mismatch')
    if type(data.get('commandsExecuted')) is not int or data['commandsExecuted'] != 0:
        raise ValueError(label + ': physical command contract invalid')
    if any(data.get(k) is not False for k in FALSE_FLAGS):
        raise ValueError(label + ': unsafe or missing guarantee')
    if runtime and data.get('mode') != 'runtime':
        raise ValueError(label + ': runtime mode mismatch')
    if not runtime and data.get('mode') == 'runtime':
        raise ValueError(label + ': offline mode mismatch')
    return {'component': label, 'passed': True, 'scenarios': count,
            'evidenceScope': 'real-systemd-runtime' if runtime else 'offline-selftest-only'}


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


def subprocess_runner(argv):
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, close_fds=True)
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT)
    except BaseException:
        stop_group(proc)
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def invoke(entry, runtime, runner=subprocess_runner):
    label, filename, command, count = entry
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise ValueError(label + ': missing or unsafe suite')
    proc = runner([sys.executable, str(path), command])
    if proc.returncode != 0:
        raise ValueError(label + ': suite exited unsuccessfully')
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(label + ': malformed JSON') from exc
    return validate(label, payload, count, runtime)


def runtime_lock():
    source = HERE / 'display-consolidated-regression-gate-v05805.py'
    if source.is_symlink() or not source.is_file():
        raise ValueError('runtime lock provider missing or unsafe')
    spec = importlib.util.spec_from_file_location('display_gate_lock_v0589', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.runtime_exclusive()


def execute(mode, runner=subprocess_runner, lock_factory=runtime_lock):
    results = [invoke(entry, False, runner) for entry in OFFLINE]
    if mode == 'runtime':
        with lock_factory():
            results.extend(invoke(entry, True, runner) for entry in RUNTIME)
    return results


def report(mode, results):
    return {'version': '0.5.89', 'mode': mode, 'selftest': 'passed',
            'totalScenarios': len(results), 'results': results,
            'systemdIntegration': mode == 'runtime',
            **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0,
            'limitations': ['Sequential cross-store observations are not atomic authentication',
                            'Runtime requires explicit opt-in and uses transient user units',
                            'No physical display commands or automatic evidence cleanup']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('offline', 'runtime'))
    mode = parser.parse_args().command
    try:
        result = report(mode, execute(mode))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'version': '0.5.89', 'mode': mode, 'selftest': 'failed',
                          'error': type(exc).__name__ + ': ' + str(exc)[:180],
                          **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
