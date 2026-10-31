#!/usr/bin/env python3
"""Serpantinum v0.5.30: offline recovery transition protocol.

No subprocesses, Hyprland commands, systemd calls, or persistent writes.
This is NOT a physical rollback implementation or execution authorization.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHASES = {'prepared', 'supervised', 'operation-reserved', 'operation-interrupted',
          'deadline-expired', 'rollback-running', 'rollback-rehearsal-verified',
          'rollback-rehearsal-incomplete', 'rollback-rehearsal-failed'}


def engine():
    spec = importlib.util.spec_from_file_location('rollback_0530', HERE / 'display-physical-rollback-engine.py')
    if spec is None or spec.loader is None:
        raise ValueError('Missing rollback rehearsal dependency')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def begin(token, deadline):
    if not isinstance(token, str) or len(token) != 24 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid transaction identity')
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or deadline <= 0:
        raise ValueError('Invalid deadline')
    return {'transactionId': token, 'deadline': deadline, 'phase': 'prepared',
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0, 'history': ['prepared']}


def transition(state, event, now, token):
    """Pure transition. Caller must provide a fresh state under its own lock."""
    if not isinstance(state, dict) or state.get('transactionId') != token:
        raise ValueError('Transaction identity mismatch')
    if state.get('phase') not in PHASES or state.get('physicalExecutionEnabled') is not False:
        raise ValueError('Invalid or physically enabled state')
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError('Invalid clock')
    phase = state['phase']
    if event == 'supervisor-ready' and phase == 'prepared' and now < state['deadline']:
        target = 'supervised'
    elif event == 'reserve' and phase == 'supervised' and now < state['deadline']:
        target = 'operation-reserved'
    elif event == 'initiator-exited' and phase == 'operation-reserved':
        target = 'operation-interrupted'
    elif event == 'expire' and phase in ('supervised', 'operation-reserved', 'operation-interrupted') and now >= state['deadline']:
        target = 'deadline-expired'
    elif event == 'recover' and phase == 'deadline-expired':
        target = 'rollback-running'
    else:
        raise ValueError('Illegal transition or deadline not satisfied')
    result = dict(state)
    result['phase'] = target
    result['history'] = [*state['history'], target]
    return result


def finish(state, saved, current, runner, observer):
    if state.get('phase') != 'rollback-running':
        raise ValueError('Recovery was not started')
    report = engine().rehearse(saved, current, runner, observer)
    if report['phase'] not in ('rollback-rehearsal-verified', 'rollback-rehearsal-incomplete', 'rollback-rehearsal-failed'):
        raise ValueError('Unknown rehearsal result')
    result = dict(state)
    result.update(phase=report['phase'], rollbackRehearsal=report,
                  physicalRollbackVerified=False, physicalExecutionEnabled=False, commandsExecuted=0)
    result['history'] = [*state['history'], report['phase']]
    return result


def scenario(kind):
    mod = engine()
    saved = mod.fixture()
    current = [dict(item) for item in saved]
    state = begin('a' * 24, 15)
    state = transition(state, 'supervisor-ready', 1, 'a' * 24)
    state = transition(state, 'reserve', 2, 'a' * 24)
    state = transition(state, 'initiator-exited', 3, 'a' * 24)
    state = transition(state, 'expire', 15, 'a' * 24)
    state = transition(state, 'recover', 15, 'a' * 24)
    if kind == 'verified':
        runner, observer = lambda _: True, lambda: current
    elif kind == 'operation-failure':
        runner, observer = lambda _: False, lambda: current
    elif kind == 'incomplete':
        runner, observer = lambda _: True, lambda: [dict(current[0], x=100), current[1]]
    elif kind == 'observer-failure':
        runner, observer = lambda _: True, lambda: (_ for _ in ()).throw(RuntimeError('observer unavailable'))
    else:
        raise ValueError('Unknown scenario')
    state = finish(state, saved, current, runner, observer)
    return {'version': '0.5.30', 'mode': 'offline-protocol-only', 'scenario': kind,
            'phase': state['phase'], 'history': state['history'],
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0, 'physicalOutputsChanged': False}


def selftest():
    expected = {'verified': 'rollback-rehearsal-verified',
                'operation-failure': 'rollback-rehearsal-failed',
                'incomplete': 'rollback-rehearsal-incomplete',
                'observer-failure': 'rollback-rehearsal-failed'}
    for name, phase in expected.items():
        assert scenario(name)['phase'] == phase
    rejected = 0
    initial = begin('a' * 24, 15)
    for state, event, now, token in [
        (initial, 'reserve', 1, 'a' * 24),
        (initial, 'supervisor-ready', 16, 'a' * 24),
        (initial, 'supervisor-ready', 1, 'b' * 24),
        (initial, 'expire', 16, 'a' * 24),
        (dict(initial, physicalExecutionEnabled=True), 'supervisor-ready', 1, 'a' * 24),
    ]:
        try:
            transition(state, event, now, token)
        except ValueError:
            rejected += 1
    assert rejected == 5
    return {'selftest': 'passed', 'scenarios': 9, 'commandsExecuted': 0,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'systemdIntegration': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'demo'])
    args = parser.parse_args()
    print(json.dumps(selftest() if args.command == 'selftest' else scenario('verified'), indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc), 'physicalExecutionEnabled': False}))
        raise SystemExit(2)
