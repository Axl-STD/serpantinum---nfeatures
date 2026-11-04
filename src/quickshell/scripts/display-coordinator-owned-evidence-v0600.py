#!/usr/bin/env python3
"""Offline evidence coordinator: owner holds lock; evidence child gets no lock FD.

This is NOT a replacement for legacy/global runtime systemd evidence paths.
"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ENV = 'SERPANTINUM_RUNTIME_ADMISSION_FD'
TARGETS = {
    'global': ('display-global-runtime-gate-v0591.py', 'offline', 2),
    'combined': ('display-evidence-security-gate-v0590.py', 'offline', 2),
    'legacy': ('display-consolidated-regression-gate-v05805.py', 'offline', 2),
}

class CoordinatorDenied(RuntimeError):
    pass


def _load_owner():
    path = HERE / 'display-admission-owner-v0600.py'
    if path.is_symlink() or not path.is_file():
        raise CoordinatorDenied('unsafe owner dependency')
    spec = importlib.util.spec_from_file_location('coordinator_owner_v0600', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stop(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()


def run(root, target='global', timeout=60):
    if target not in TARGETS or type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise CoordinatorDenied('invalid target or timeout')
    if ENV in os.environ:
        raise CoordinatorDenied('inherited admission forbidden')
    name, command, expected = TARGETS[target]
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise CoordinatorDenied('unsafe evidence target')
    owner = _load_owner()
    with owner.owner_only(root):
        env = os.environ.copy()
        env.pop(ENV, None)
        env.pop('PYTHONPATH', None)
        proc = subprocess.Popen([sys.executable, '-B', str(path), command],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, close_fds=True, start_new_session=True, env=env)
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:
            _stop(proc)
            raise
        if proc.returncode != 0:
            raise CoordinatorDenied('offline evidence failed: ' + err[:120])
        try:
            data = json.loads(out)
        except (ValueError, TypeError) as exc:
            raise CoordinatorDenied('invalid evidence JSON') from exc
        if (type(data) is not dict or data.get('selftest') != 'passed' or
                data.get('mode') != 'offline' or
                type(data.get('totalScenarios')) is not int or
                data['totalScenarios'] != expected or
                type(data.get('results')) is not list or
                len(data['results']) != expected or
                any(type(row) is not dict or row.get('passed') is not True for row in data['results']) or
                data.get('physicalExecutionEnabled') is not False or
                data.get('executionAuthorized') is not False or
                type(data.get('commandsExecuted')) is not int or data['commandsExecuted'] != 0):
            raise CoordinatorDenied('unsafe evidence report')
        return {'version':'0.6.0','mode':'offline-coordinator-owned-evidence',
            'selftest':'passed','target':target,'evidenceScenarios':expected,
            'workerLockFdInherited':False,'authenticatedDelegationImplemented':False,
            'systemdIntegration':False,'executionAuthorized':False,
            'physicalExecutionEnabled':False,'commandsExecuted':0,
            'limitations':['Offline evidence only, no runtime systemd execution',
                'No authenticated worker delegation or physical rollback',
                'Cooperative lock exclusion only']}


if __name__ == '__main__':
    raise SystemExit('import-only offline coordinator')
