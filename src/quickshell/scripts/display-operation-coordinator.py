#!/usr/bin/env python3
"""Serpantinum v0.5.19: read-only, lock-scoped operation rehearsal.

This is NOT a physical executor or an authorization to execute commands.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rehearse(plan, bundle, watchdog, precheck, guard, query_unit, clock=time.time, on_locked=None):
    folder = bundle.runtime_dir()
    with bundle.locked(folder):
        initial = bundle.read_bundle(folder)
        initial_state = watchdog.read_state(folder)
        if initial is None:
            raise ValueError('No sealed recovery bundle')
        token = initial.get('id')
        if not isinstance(token, str) or not precheck.TOKEN.fullmatch(token):
            raise ValueError('Invalid transaction identity')
        if (folder / 'cleanup-receipt.json').exists():
            raise ValueError('Cleanup receipt exists')
    unit = 'serpantinum-display-v0514-' + token + '.service'
    unit_state = query_unit(unit)  # Never hold the lock across systemctl.
    with bundle.locked(folder):
        current = bundle.read_bundle(folder)
        state = watchdog.read_state(folder)
        if current != initial or state != initial_state:
            raise ValueError('Transaction changed during supervisor query')
        if (folder / 'cleanup-receipt.json').exists():
            raise ValueError('Cleanup started')
        deadline = state.get('deadline')
        if isinstance(deadline, bool) or not isinstance(deadline, (float, int)) or clock() >= deadline:
            raise ValueError('Transaction expired')
        result = precheck.assess(current, state, plan, unit_state, guard.inspect_plan)
        if on_locked is not None:
            on_locked()  # Test hook: lock is still held here.
        # No subprocess, monitor command, or state transition is performed.
        return {'version': '0.5.19', 'mode': 'simulation-only',
                'phase': 'protected-operation-rehearsed',
                'transactionId': token, 'lockHeldDuringFinalValidation': True,
                'structurallyValid': result['structurallyValid'],
                'safeToExecuteAutomatically': False, 'physicalExecutionEnabled': False,
                'commandsExecuted': 0, 'physicalOutputsChanged': False,
                'physicalRollbackVerified': False}


def inspect(plan):
    bundle = load('display-recovery-bundle.py', 'bundle_0519')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_0519')
    precheck = load('display-executor-precheck.py', 'precheck_0519')
    guard = load('display-executor-guard.py', 'guard_0519')
    def query(unit):
        proc = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                              capture_output=True, text=True, timeout=6)
        return 'active' if proc.returncode == 0 and 'ActiveState=active' in proc.stdout.splitlines() else 'unknown'
    return rehearse(plan, bundle, watchdog, precheck, guard, query)


def selftest():
    import tempfile
    from contextlib import contextmanager
    from types import SimpleNamespace
    precheck = load('display-executor-precheck.py', 'precheck_test_0519')
    guard = load('display-executor-guard.py', 'guard_test_0519')
    token = 'a' * 24
    payload = {'id': token, 'phase': 'sealed-read-only', 'safeToExecuteAutomatically': False,
               'snapshot': {'outputs': [{'name': 'eDP-2'}]}}
    state = {'transactionId': token, 'pending': True, 'watchdogReady': True,
             'mode': 'simulation-only', 'phase': 'simulated-pending',
             'unit': 'serpantinum-display-v0514-' + token + '.service',
             'physicalRollbackVerified': False, 'deadline': 1000.0}
    plan = {'mode': 'dry-run-only', 'safeToExecuteAutomatically': False,
            'actions': [{'output': 'eDP-2', 'previewOnly': True,
                         'argv': ['hyprctl', 'keyword', 'monitor',
                                  'eDP-2,1920x1080@60.06,0x0,1,transform,0,vrr,0']}]}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        held = [False]
        @contextmanager
        def lock(path):
            assert not held[0]
            held[0] = True
            try:
                yield
            finally:
                held[0] = False
        bundle = SimpleNamespace(runtime_dir=lambda: folder, locked=lock,
                                 read_bundle=lambda path: dict(payload))
        live = dict(state)
        watchdog = SimpleNamespace(read_state=lambda path: dict(live))
        observed = []
        def query(unit):
            assert not held[0]
            observed.append(unit)
            return 'active'
        result = rehearse(plan, bundle, watchdog, precheck, guard, query,
                          clock=lambda: 500, on_locked=lambda: observed.append(held[0]))
        assert result['commandsExecuted'] == 0 and observed[-1] is True
        assert len(observed) == 2 and not held[0]
        def reject(fn):
            try:
                fn()
            except ValueError:
                return
            raise AssertionError('Unsafe operation accepted')
        reject(lambda: rehearse(plan, bundle, watchdog, precheck, guard,
                                lambda unit: 'inactive', clock=lambda: 500))
        reject(lambda: rehearse(plan, bundle, watchdog, precheck, guard,
                                query, clock=lambda: 1000))
        def change(unit):
            live['phase'] = 'verification-running'
            return 'active'
        reject(lambda: rehearse(plan, bundle, watchdog, precheck, guard,
                                change, clock=lambda: 500))
        live['phase'] = 'simulated-pending'
        reject(lambda: rehearse(dict(plan, actions=[]), bundle, watchdog, precheck,
                                guard, query, clock=lambda: 500))
        (folder / 'cleanup-receipt.json').write_text('{}')
        reject(lambda: rehearse(plan, bundle, watchdog, precheck, guard,
                                query, clock=lambda: 500))
    return {'selftest': 'passed', 'scenarios': 6, 'commandsExecuted': 0,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'inspect'])
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'selftest':
            result = selftest()
        else:
            if args.plan is None or not args.plan.is_file() or args.plan.stat().st_size > 1024 * 1024:
                raise ValueError('Missing or oversized plan')
            result = inspect(json.loads(args.plan.read_text(encoding='utf-8')))
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
