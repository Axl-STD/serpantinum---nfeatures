#!/usr/bin/env python3
"""Serpantinum v0.5.21: simulation-only rollback rehearsal.

Uses the existing planner and observable-state verifier. Never invokes hyprctl,
executes plan argv, changes displays, or writes persistent configuration.
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


def rehearse(saved, current, runner, observe):
    """Runner and observer are injected simulations, NEVER subprocess executors."""
    planner = load('display-restore-plan.py', 'restore_plan_0521')
    verifier = load('display-restore-verify.py', 'restore_verify_0521')
    actions = planner.plan(saved, current)['actions']
    records = []
    failed = False
    for action in actions:
        # Never call any argv from the plan. Pass only an output identifier to
        # the injected simulator, preventing accidental command execution.
        name = action['output']
        try:
            result = runner(name)
            if type(result) is not bool:
                raise ValueError('Simulator must return a boolean')
            records.append({'output': name, 'simulatedSuccess': result})
            if not result:
                failed = True
                break
        except Exception as exc:
            records.append({'output': name, 'simulatedSuccess': False, 'error': str(exc)})
            failed = True
            break
    report = None
    observation_error = None
    try:
        observed = observe()
        report = verifier.compare(saved, observed)
    except Exception as exc:
        observation_error = str(exc)
    equivalent = report['equivalentObservableState'] if report else None
    if failed or observation_error is not None:
        phase = 'rollback-failed'
    elif equivalent:
        phase = 'rollback-verified'
    else:
        phase = 'rollback-incomplete'
    return {
        'schemaVersion': 1, 'version': '0.5.21', 'mode': 'simulation-only',
        'phase': phase, 'simulatedRollbackVerified': phase == 'rollback-verified',
        'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
        'commandsExecuted': 0, 'physicalOutputsChanged': False,
        'persistentConfigChanged': False,
        'simulatedActions': records, 'equivalentObservableState': equivalent,
        'differences': report['differences'] if report else [],
        'observationError': observation_error,
        'note': 'Injected simulation only; no physical recovery was attempted'
    }


def fixtures():
    a = {'name': 'eDP-2', 'disabled': False, 'mirrorOf': 'none', 'width': 1920,
         'height': 1080, 'refreshRate': 60.061, 'x': 0, 'y': 0,
         'scale': 1.0, 'transform': 0, 'availableModes': ['1920x1080@60.06Hz']}
    b = dict(a, name='HDMI-A-1', x=1920, refreshRate=60.0,
             availableModes=['1920x1080@60.00Hz'])
    return [a, b]


def selftest():
    saved = fixtures()
    current = [dict(x) for x in saved]
    ok = rehearse(saved, current, lambda name: True, lambda: current)
    assert ok['phase'] == 'rollback-verified' and len(ok['simulatedActions']) == 2
    fail = rehearse(saved, current, lambda name: False, lambda: current)
    assert fail['phase'] == 'rollback-failed' and len(fail['simulatedActions']) == 1
    partial = rehearse(saved, current, lambda name: True,
                       lambda: [dict(current[0], x=100), current[1]])
    assert partial['phase'] == 'rollback-incomplete' and partial['differences']
    external = rehearse(saved, current, lambda name: True,
                        lambda: [current[0], dict(current[1], refreshRate=50.0)])
    assert external['phase'] == 'rollback-incomplete'
    unavailable = rehearse(saved, current, lambda name: True,
                           lambda: (_ for _ in ()).throw(RuntimeError('observer unavailable')))
    assert unavailable['phase'] == 'rollback-failed'
    try:
        rehearse([dict(saved[0], disabled=True), saved[1]], current,
                 lambda name: True, lambda: current)
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid restoration plan accepted')
    assert all(not result['physicalRollbackVerified'] and result['commandsExecuted'] == 0
               for result in (ok, fail, partial, external, unavailable))
    return {'selftest': 'passed', 'scenarios': 6, 'commandsExecuted': 0,
            'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'demo'])
    args = parser.parse_args()
    if args.command == 'selftest':
        result = selftest()
    else:
        saved = fixtures()
        result = rehearse(saved, saved, lambda name: True, lambda: saved)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
