#!/usr/bin/env python3
"""Serpantinum v0.5.27: physical rollback protocol rehearsal ONLY.

The runner receives an output name, never executable arguments. No real
Hyprland command execution, systemd service changes or persistent edits.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def dependency(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rehearse(saved, current, runner, observer):
    """Rehearse a planned rollback; runner and observer must be injected."""
    planner = dependency('display-restore-plan.py', 'planner_0527')
    verifier = dependency('display-restore-verify.py', 'verifier_0527')
    planned = planner.plan(saved, current)
    if planned.get('safeToExecuteAutomatically') is not False or planned.get('mode') != 'dry-run-only':
        raise ValueError('Unsafe rollback plan')
    actions = planned['actions']
    if not actions or any(a.get('previewOnly') is not True for a in actions):
        raise ValueError('Non-preview action rejected')
    records = []
    failed = False
    for action in actions:
        name = action['output']
        try:
            success = runner(name)
            if type(success) is not bool:
                raise ValueError('Injected runner must return boolean')
            records.append({'output': name, 'simulatedSuccess': success})
            if not success:
                failed = True
                break
        except Exception as exc:
            records.append({'output': name, 'simulatedSuccess': False, 'error': str(exc)[:300]})
            failed = True
            break
    differences = []
    observation_error = None
    equivalent = False
    try:
        observed = observer()
        report = verifier.compare(saved, observed)
        equivalent = report.get('equivalentObservableState') is True
        differences = report.get('differences', [])
    except Exception as exc:
        observation_error = str(exc)[:300]
    if failed or observation_error is not None:
        phase = 'rollback-rehearsal-failed'
    elif equivalent:
        phase = 'rollback-rehearsal-verified'
    else:
        phase = 'rollback-rehearsal-incomplete'
    return {'schemaVersion': 1, 'version': '0.5.27', 'mode': 'injected-rehearsal-only',
            'phase': phase, 'simulatedRollbackVerified': phase == 'rollback-rehearsal-verified',
            'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
            'physicalExecutionEnabled': False, 'commandsExecuted': 0,
            'physicalOutputsChanged': False, 'persistentConfigChanged': False,
            'simulatedActions': records, 'equivalentObservableState': equivalent,
            'differences': differences, 'observationError': observation_error,
            'note': 'Injected test only. No physical commands were executed.'}


def fixture():
    a = {'name': 'eDP-1', 'disabled': False, 'mirrorOf': 'none', 'width': 1920,
         'height': 1080, 'refreshRate': 60.061, 'x': 0, 'y': 0, 'scale': 1.0,
         'transform': 0, 'availableModes': ['1920x1080@60.06Hz']}
    b = dict(a, name='HDMI-A-1', x=1920, refreshRate=60.0,
             availableModes=['1920x1080@60.00Hz'])
    return [a, b]


def selftest():
    saved = fixture()
    current = [dict(x) for x in saved]
    cases = [
        ('equivalent', lambda _: True, lambda: current, 'rollback-rehearsal-verified'),
        ('operation-failure', lambda _: False, lambda: current, 'rollback-rehearsal-failed'),
        ('partial', lambda _: True, lambda: [dict(current[0], x=100), current[1]], 'rollback-rehearsal-incomplete'),
        ('external-change', lambda _: True, lambda: [current[0], dict(current[1], refreshRate=50)], 'rollback-rehearsal-incomplete'),
        ('observer-failure', lambda _: True, lambda: (_ for _ in ()).throw(RuntimeError('unavailable')), 'rollback-rehearsal-failed'),
        ('runner-exception', lambda _: (_ for _ in ()).throw(RuntimeError('injected failure')), lambda: current, 'rollback-rehearsal-failed'),
    ]
    for label, runner, observer, expected in cases:
        result = rehearse(saved, current, runner, observer)
        assert result['phase'] == expected, label
        assert result['commandsExecuted'] == 0 and result['physicalRollbackVerified'] is False
        assert result['physicalExecutionEnabled'] is False
    rejected = 0
    for bad in ([dict(saved[0], disabled=True), saved[1]],
                [dict(saved[0], name='eDP-2'), saved[1]],
                [dict(saved[0], availableModes=[]), saved[1]]):
        try:
            rehearse(bad, current, lambda _: True, lambda: current)
        except ValueError:
            rejected += 1
        else:
            raise AssertionError('Unsafe plan accepted')
    return {'selftest': 'passed', 'scenarios': len(cases) + rejected,
            'commandsExecuted': 0, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'demo'])
    args = parser.parse_args()
    result = selftest() if args.command == 'selftest' else rehearse(fixture(), fixture(), lambda _: True, fixture)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, AssertionError) as exc:
        print(json.dumps({'error': str(exc), 'physicalExecutionEnabled': False,
                          'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
