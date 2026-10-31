#!/usr/bin/env python3
"""Display recovery state machine v0.5.7: offline simulation only.

Never invokes hyprctl, systemd, or a shell. It never claims physical recovery.
Consumes v0.5 snapshot JSON and the v0.5.6 comparison implementation.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def verifier():
    path = HERE / 'display-restore-verify.py'
    if not path.is_file():
        raise ValueError('Missing display-restore-verify.py alongside integration script')
    spec = importlib.util.spec_from_file_location('serpantinum_display_verify', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def simulate(expected, observed, scenario):
    if scenario not in ('equivalent', 'incomplete', 'failure'):
        raise ValueError('Unknown scenario')
    result = {'schemaVersion': 1, 'version': '0.5.7', 'mode': 'simulation-only',
              'physicalOutputsChanged': False, 'persistentConfigChanged': False,
              'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
              'states': ['prepared', 'pending', 'rollback-running']}
    if scenario == 'failure':
        result.update(phase='rollback-failed', simulatedFailure=True,
                      equivalentObservableState=None, differences=[],
                      note='Injected failure; no recovery attempted')
    else:
        report = verifier().compare(expected, observed)
        equivalent = report['equivalentObservableState']
        result.update(phase='rollback-verified' if equivalent else 'rollback-incomplete',
                      equivalentObservableState=equivalent, differences=report['differences'],
                      note='Verified snapshots ONLY; physical rollback NOT performed')
    result['states'].append(result['phase'])
    return result


def sample():
    return [{'name': 'eDP-2', 'width': 1920, 'height': 1080, 'x': 0, 'y': 0,
             'scale': 1.0, 'transform': 0, 'disabled': False, 'mirrorOf': 'none',
             'refreshRate': 60.061}]


def selftest():
    a = sample()
    ok = simulate(a, [dict(a[0])], 'equivalent')
    bad = simulate(a, [dict(a[0], x=100)], 'incomplete')
    failed = simulate(a, [dict(a[0])], 'failure')
    assert ok['phase'] == 'rollback-verified' and ok['equivalentObservableState']
    assert bad['phase'] == 'rollback-incomplete' and bad['differences']
    assert failed['phase'] == 'rollback-failed' and failed['simulatedFailure']
    assert all(not r['physicalRollbackVerified'] and not r['physicalRollbackAttempted']
               for r in (ok, bad, failed))
    return {'selftest': 'passed', 'scenarios': ['equivalent', 'incomplete', 'failure'],
            'physicalOutputsChanged': False, 'physicalRollbackVerified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('selftest')
    run = sub.add_parser('simulate')
    run.add_argument('--snapshot', type=Path, required=True)
    run.add_argument('--current', type=Path, required=True)
    run.add_argument('--scenario', choices=['equivalent', 'incomplete', 'failure'], required=True)
    args = p.parse_args()
    if args.command == 'selftest':
        result = selftest()
    else:
        verify = verifier()
        result = simulate(verify.read(args.snapshot), verify.read(args.current), args.scenario)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if args.command == 'selftest' or result['phase'] == 'rollback-verified' else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, AssertionError, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(1)
