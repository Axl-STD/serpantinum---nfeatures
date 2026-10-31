#!/usr/bin/env python3
"""Serpantinum v0.5.26: transaction-bound command preview, NEVER physical execution.

No hyprctl keyword, no service launch, no persistent configuration edits.
Native Hyprland parser is invoked only with --verify-config on temporary data.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preview(plan, bundle, watchdog, precheck, guard, native, query_unit, clock=time.time,
            native_verify=None, on_locked=None):
    """Inspect a plan without executing it; reject state changes across external checks."""
    folder = bundle.runtime_dir()
    with bundle.locked(folder):
        initial_bundle = bundle.read_bundle(folder)
        initial_state = watchdog.read_state(folder)
        if initial_bundle is None:
            raise ValueError('No sealed recovery bundle')
        if (folder / 'cleanup-receipt.json').exists() or (folder / 'cleanup-receipt.json').is_symlink():
            raise ValueError('Cleanup evidence exists')
        token = initial_bundle.get('id')
        if not isinstance(token, str) or not precheck.TOKEN.fullmatch(token):
            raise ValueError('Invalid transaction identity')
        if initial_state.get('transactionId') != token:
            raise ValueError('Transaction identity mismatch')
    unit = 'serpantinum-display-v0514-' + token + '.service'
    unit_state = query_unit(unit)
    if unit_state != 'active':
        raise ValueError('Supervisor not active')
    native_report = (native_verify or native.verify)(plan)
    if native_report.get('nativeConfigParserAccepted') is not True:
        raise ValueError('Native Hyprland parser rejected plan')
    with bundle.locked(folder):
        payload = bundle.read_bundle(folder)
        state = watchdog.read_state(folder)
        if payload != initial_bundle or state != initial_state:
            raise ValueError('Transaction changed during external verification')
        if (folder / 'cleanup-receipt.json').exists() or (folder / 'cleanup-receipt.json').is_symlink():
            raise ValueError('Cleanup started')
        deadline = state.get('deadline')
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or clock() >= deadline:
            raise ValueError('Transaction expired')
        precheck.assess(payload, state, plan, unit_state, guard.inspect_plan)
        if on_locked:
            on_locked()
        commands = [{'output': action['output'], 'argv': list(action['argv']),
                     'previewOnly': True, 'executed': False} for action in plan['actions']]
        return {'schemaVersion': 1, 'version': '0.5.26', 'mode': 'protected-preview-only',
                'phase': 'physical-executor-disabled', 'transactionId': token,
                'lockHeldDuringFinalValidation': True, 'structurallyValid': True,
                'nativeConfigParserAccepted': True, 'supervisorObservedActive': True,
                'commands': commands, 'safeToExecuteAutomatically': False,
                'physicalExecutionEnabled': False, 'commandsExecuted': 0,
                'physicalOutputsChanged': False, 'persistentConfigChanged': False,
                'physicalRollbackVerified': False,
                'limitations': ['Preview is not execution authorization',
                                'External compositor changes cannot be excluded',
                                'No physical rollback capability']}


def inspect(plan):
    bundle = load('display-recovery-bundle.py', 'bundle_0526')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_0526')
    precheck = load('display-executor-precheck.py', 'precheck_0526')
    guard = load('display-executor-guard.py', 'guard_0526')
    native = load('display-native-verify.py', 'native_0526')
    def query(unit):
        result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                                capture_output=True, text=True, timeout=6)
        return 'active' if result.returncode == 0 and 'ActiveState=active' in result.stdout.splitlines() else 'unknown'
    return preview(plan, bundle, watchdog, precheck, guard, native, query)


def selftest():
    import tempfile
    from contextlib import contextmanager
    from types import SimpleNamespace
    precheck = load('display-executor-precheck.py', 'precheck_test_0526')
    guard = load('display-executor-guard.py', 'guard_test_0526')
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
        def lock(_):
            assert not held[0]
            held[0] = True
            try:
                yield
            finally:
                held[0] = False
        bundle = SimpleNamespace(runtime_dir=lambda: folder, locked=lock,
                                 read_bundle=lambda _: dict(payload))
        live = dict(state)
        watchdog = SimpleNamespace(read_state=lambda _: dict(live))
        native = SimpleNamespace(verify=lambda _: {'nativeConfigParserAccepted': True})
        def query(_):
            assert not held[0]
            return 'active'
        def run(p=plan, q=query, nv=native.verify, now=500):
            return preview(p, bundle, watchdog, precheck, guard, native, q,
                           clock=lambda: now, native_verify=nv,
                           on_locked=lambda: held[0] or (_ for _ in ()).throw(AssertionError('Lock lost')))
        good = run()
        assert good['commandsExecuted'] == 0 and not good['physicalExecutionEnabled']
        assert good['commands'][0]['executed'] is False
        count = 1
        def rejects(fn):
            nonlocal count
            try:
                fn()
            except ValueError:
                count += 1
                return
            raise AssertionError('Unsafe preview accepted')
        rejects(lambda: run(q=lambda _: 'inactive'))
        rejects(lambda: run(nv=lambda _: {'nativeConfigParserAccepted': False}))
        rejects(lambda: run(now=1000))
        rejects(lambda: run(p=dict(plan, actions=[])))
        def mutate(_):
            live['phase'] = 'verification-running'
            return {'nativeConfigParserAccepted': True}
        rejects(lambda: run(nv=mutate))
        live['phase'] = 'simulated-pending'
        (folder / 'cleanup-receipt.json').write_text('{}')
        rejects(lambda: run())
        (folder / 'cleanup-receipt.json').unlink()
        payload['id'] = 'b' * 24
        rejects(lambda: run())
    return {'selftest': 'passed', 'scenarios': count, 'commandsExecuted': 0,
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
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False,
                          'physicalExecutionEnabled': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
