#!/usr/bin/env python3
"""Serpantinum v0.5.17: read-only transaction-bound executor precheck.

NEVER runs hyprctl keyword, starts services, or changes monitor configuration.
This is NOT an authorization to execute physical changes.
"""
import argparse
import importlib.util
import json
import time
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')


def load(filename, name):
    path = HERE / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assess(payload, state, plan, unit_state, inspect_plan):
    if not isinstance(payload, dict) or not isinstance(state, dict):
        raise ValueError('Invalid transaction state')
    token = payload.get('id')
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    if state.get('transactionId') != token:
        raise ValueError('Transaction identity mismatch')
    if payload.get('phase') != 'sealed-read-only' or payload.get('safeToExecuteAutomatically') is not False:
        raise ValueError('Invalid recovery bundle')
    if state.get('pending') is not True or state.get('watchdogReady') is not True:
        raise ValueError('Supervisor not ready or transaction not pending')
    if state.get('mode') != 'simulation-only' or state.get('phase') != 'simulated-pending':
        raise ValueError('Not a simulation transaction')
    if state.get('unit') != 'serpantinum-display-v0514-' + token + '.service' or unit_state != 'active':
        raise ValueError('Independent supervisor not active')
    if state.get('physicalRollbackVerified') is not False:
        raise ValueError('Unexpected physical rollback claim')
    validated = inspect_plan(plan)
    if validated.get('structurallyValid') is not True:
        raise ValueError('Structural validation failed')
    outputs = payload.get('snapshot', {}).get('outputs')
    if not isinstance(outputs, list) or not outputs:
        raise ValueError('Missing snapshot outputs')
    expected = {o.get('name') for o in outputs if isinstance(o, dict)}
    observed = {o['output'] for o in validated['outputs']}
    if None in expected or len(expected) != len(outputs) or expected != observed:
        raise ValueError('Plan outputs do not match sealed snapshot')
    return {'version': '0.5.17', 'mode': 'simulation-only', 'phase': 'executor-precheck-passed',
            'transactionId': token, 'supervisorActive': True, 'bundleIntegrityVerified': True,
            'structurallyValid': True, 'outputs': sorted(observed),
            'safeToExecuteAutomatically': False, 'physicalExecutionEnabled': False,
            'commandsExecuted': 0, 'physicalOutputsChanged': False,
            'physicalRollbackVerified': False}


def inspect_with_dependencies(plan, bundle, watchdog, guard, unit_query, clock=time.time):
    """Read, query systemd unlocked, then revalidate under the same lock."""
    folder = bundle.runtime_dir()
    with bundle.locked(folder):
        initial_bundle = bundle.read_bundle(folder)
        initial_state = watchdog.read_state(folder)
        if initial_bundle is None:
            raise ValueError('No sealed recovery bundle')
        if (folder / 'cleanup-receipt.json').exists():
            raise ValueError('Cleanup receipt exists')
        token = initial_bundle.get('id')
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('Invalid transaction identity')
        if initial_state.get('transactionId') != token:
            raise ValueError('Transaction identity mismatch')
    unit = 'serpantinum-display-v0514-' + token + '.service'
    unit_state = unit_query(unit)
    with bundle.locked(folder):
        payload = bundle.read_bundle(folder)
        state = watchdog.read_state(folder)
        if payload is None or (folder / 'cleanup-receipt.json').exists():
            raise ValueError('Recovery bundle removed or cleanup started')
        if payload != initial_bundle or state != initial_state:
            raise ValueError('Transaction changed during external supervisor query')
        deadline = state.get('deadline')
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or clock() >= deadline:
            raise ValueError('Transaction deadline expired or invalid')
        result = assess(payload, state, plan, unit_state, guard.inspect_plan)
        result['version'] = '0.5.18.1'
        result['doubleCheckedUnderLock'] = True
        result['physicalExecutionEnabled'] = False
        return result


def inspect(plan):
    bundle = load('display-recovery-bundle.py', 'bundle_05181')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_05181')
    guard = load('display-executor-guard.py', 'guard_05181')

    def unit_query(unit):
        result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                                capture_output=True, text=True, timeout=6)
        return 'active' if result.returncode == 0 and 'ActiveState=active' in result.stdout.splitlines() else 'unknown'

    return inspect_with_dependencies(plan, bundle, watchdog, guard, unit_query)


def selftest():
    guard = load('display-executor-guard.py', 'guard_test_0517')
    token = 'a' * 24
    payload = {'id': token, 'phase': 'sealed-read-only', 'safeToExecuteAutomatically': False,
               'snapshot': {'outputs': [{'name': 'eDP-2'}]}}
    state = {'transactionId': token, 'pending': True, 'watchdogReady': True,
             'mode': 'simulation-only', 'phase': 'simulated-pending',
             'unit': 'serpantinum-display-v0514-' + token + '.service',
             'physicalRollbackVerified': False}
    plan = {'mode': 'dry-run-only', 'safeToExecuteAutomatically': False,
            'actions': [{'output': 'eDP-2', 'previewOnly': True,
                         'argv': ['hyprctl', 'keyword', 'monitor', 'eDP-2,1920x1080@60.06,0x0,1,transform,0,vrr,0']}]}
    assert assess(payload, state, plan, 'active', guard.inspect_plan)['commandsExecuted'] == 0
    scenarios = [(dict(state, transactionId='b' * 24), plan, 'active'),
                 (dict(state, pending=False), plan, 'active'),
                 (state, plan, 'inactive'),
                 (state, dict(plan, actions=[]), 'active')]
    for altered_state, altered_plan, unit in scenarios:
        try:
            assess(payload, altered_state, altered_plan, unit, guard.inspect_plan)
        except ValueError:
            continue
        raise AssertionError('Unsafe transaction accepted')
    # Simulate a state transition occurring during the unlocked systemd query.
    import tempfile
    from types import SimpleNamespace
    from contextlib import contextmanager
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        class FakeBundle:
            def runtime_dir(self):
                return folder
            @contextmanager
            def locked(self, path):
                yield
            def read_bundle(self, path):
                return payload
        live_state = dict(state, deadline=1000.0)
        fake_watchdog = SimpleNamespace(read_state=lambda path: dict(live_state))
        fake_guard = SimpleNamespace(inspect_plan=guard.inspect_plan)
        def changed_query(unit):
            live_state['phase'] = 'verification-running'
            return 'active'
        try:
            inspect_with_dependencies(plan, FakeBundle(), fake_watchdog, fake_guard,
                                      changed_query, clock=lambda: 500.0)
        except ValueError as exc:
            assert 'changed during' in str(exc)
        else:
            raise AssertionError('Race condition not detected')
        live_state['phase'] = 'simulated-pending'
        approved = inspect_with_dependencies(plan, FakeBundle(), fake_watchdog, fake_guard,
                                             lambda unit: 'active', clock=lambda: 500.0)
        assert approved['doubleCheckedUnderLock'] and approved['commandsExecuted'] == 0
        try:
            inspect_with_dependencies(plan, FakeBundle(), fake_watchdog, fake_guard,
                                      lambda unit: 'active', clock=lambda: 1000.0)
        except ValueError as exc:
            assert 'deadline' in str(exc)
        else:
            raise AssertionError('Expired transaction accepted')
    return {'selftest': 'passed', 'scenarios': 8, 'commandsExecuted': 0,
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
