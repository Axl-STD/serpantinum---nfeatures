#!/usr/bin/env python3
"""v0.5.22: read-only post-expiry rollback rehearsal.

Does not run inside systemd watchdog; it inspects an already completed watchdog.
No monitor command is executed, and no transaction state is written.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def validate(payload, state):
    if not isinstance(payload, dict) or not isinstance(state, dict):
        raise ValueError('Missing or invalid transaction evidence')
    token = payload.get('id')
    if not isinstance(token, str) or len(token) != 24 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid transaction identifier')
    if state.get('transactionId') != token:
        raise ValueError('Bundle and watchdog identity mismatch')
    if state.get('pending') is not False or state.get('phase') not in ('snapshot-equivalent', 'snapshot-different'):
        raise ValueError('Watchdog has not completed observable verification')
    if state.get('mode') != 'simulation-only' or state.get('physicalRollbackVerified') is not False:
        raise ValueError('Unexpected physical rollback claim')
    if state.get('integrityVerified') is not True:
        raise ValueError('Watchdog integrity not verified')
    if state.get('unit') != 'serpantinum-display-v0514-' + token + '.service':
        raise ValueError('Unexpected watchdog unit')
    if payload.get('phase') != 'sealed-read-only' or payload.get('safeToExecuteAutomatically') is not False:
        raise ValueError('Unsafe recovery bundle')
    snapshot = payload.get('snapshot')
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('outputs'), list):
        raise ValueError('Invalid saved snapshot')
    return snapshot['outputs'], token

def evaluate(payload, state, scenario, engine, observed=None):
    saved, token = validate(payload, state)
    current = [dict(x) for x in saved]
    if scenario == 'equivalent':
        runner, observer = lambda name: True, lambda: current
    elif scenario == 'command-failure':
        runner, observer = lambda name: False, lambda: current
    elif scenario == 'incomplete':
        altered = [dict(x) for x in current]
        altered[0]['x'] = altered[0]['x'] + 100
        runner, observer = lambda name: True, lambda: altered
    elif scenario == 'observer-failure':
        runner = lambda name: True
        def observer():
            raise RuntimeError('Injected observer failure')
    else:
        raise ValueError('Unsupported scenario')
    result = engine.rehearse(saved, current, runner, observer)
    return {'version': '0.5.22', 'mode': 'post-expiry-simulation-only',
            'transactionId': token, 'watchdogPhase': state['phase'],
            'scenario': scenario, 'rollbackSimulation': result,
            'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0, 'systemdRollbackIntegration': False,
            'note': 'Offline rehearsal after completed watchdog; not automatic rollback'}

def inspect(scenario):
    bundle = load('display-recovery-bundle.py', 'bundle_0522')
    watchdog = load('display-sealed-watchdog.py', 'watchdog_0522')
    engine = load('display-rollback-engine.py', 'engine_0522')
    folder = bundle.runtime_dir()
    with bundle.locked(folder):
        payload = bundle.read_bundle(folder)
        state = watchdog.read_state(folder)
    if payload is None:
        raise ValueError('No sealed recovery bundle')
    # Do not confuse a completed state with an active service.
    if watchdog.active(state.get('unit', '')):
        raise ValueError('Watchdog unit is still active')
    return evaluate(payload, state, scenario, engine)

def selftest():
    engine = load('display-rollback-engine.py', 'engine_test_0522')
    saved = engine.fixtures()
    token = 'a' * 24
    payload = {'id': token, 'phase': 'sealed-read-only', 'safeToExecuteAutomatically': False,
               'snapshot': {'outputs': saved}}
    state = {'transactionId': token, 'pending': False, 'phase': 'snapshot-equivalent',
             'mode': 'simulation-only', 'physicalRollbackVerified': False,
             'integrityVerified': True, 'unit': 'serpantinum-display-v0514-' + token + '.service'}
    expected = {'equivalent': 'rollback-verified', 'command-failure': 'rollback-failed',
                'incomplete': 'rollback-incomplete', 'observer-failure': 'rollback-failed'}
    for scenario, phase in expected.items():
        report = evaluate(payload, state, scenario, engine)
        assert report['rollbackSimulation']['phase'] == phase
        assert report['commandsExecuted'] == 0 and not report['physicalRollbackVerified']
    for bad_state in (dict(state, pending=True), dict(state, transactionId='b'*24),
                      dict(state, integrityVerified=False), dict(state, phase='verification-failed')):
        try:
            evaluate(payload, bad_state, 'equivalent', engine)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe state accepted')
    return {'selftest': 'passed', 'scenarios': 8, 'commandsExecuted': 0,
            'systemdRollbackIntegration': False, 'physicalRollbackVerified': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'inspect'])
    parser.add_argument('--scenario', choices=['equivalent', 'command-failure', 'incomplete', 'observer-failure'], default='equivalent')
    args = parser.parse_args()
    result = selftest() if args.command == 'selftest' else inspect(args.scenario)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if args.command == 'selftest' or result['rollbackSimulation']['phase'] == 'rollback-verified' else 2

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
