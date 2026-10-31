#!/usr/bin/env python3
"""Compare v0.5 snapshot outputs against observed Hyprland outputs. Read-only.

No subprocess execution, configuration edits, or display changes.
"""
import argparse
import json
import math
import sys
from pathlib import Path

FIELDS = ('width', 'height', 'x', 'y', 'scale', 'transform', 'disabled', 'mirrorOf')

def read(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Snapshot exceeds 1 MiB')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or not isinstance(data.get('outputs'), list):
        raise ValueError('Expected schemaVersion 1 and outputs list')
    return data['outputs']

def indexed(outputs):
    result = {}
    for output in outputs:
        if not isinstance(output, dict) or not isinstance(output.get('name'), str) or not output['name'] or output['name'] in result:
            raise ValueError('Invalid or duplicate output name')
        result[output['name']] = output
    return result

def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)

def compare(expected, observed, hz_tolerance=0.15, scale_tolerance=0.001):
    if not numeric(hz_tolerance) or not 0 <= hz_tolerance <= 0.5:
        raise ValueError('Invalid Hz tolerance')
    if not numeric(scale_tolerance) or not 0 <= scale_tolerance <= 0.01:
        raise ValueError('Invalid scale tolerance')
    before, after = indexed(expected), indexed(observed)
    differences = []
    for name in sorted(set(before) | set(after)):
        if name not in before or name not in after:
            differences.append({'output': name, 'field': 'presence', 'expected': name in before, 'observed': name in after})
            continue
        a, b = before[name], after[name]
        for field in FIELDS + ('refreshRate',):
            av, bv = a.get(field), b.get(field)
            if field in ('scale', 'refreshRate'):
                tolerance = scale_tolerance if field == 'scale' else hz_tolerance
                equal = numeric(av) and numeric(bv) and abs(av - bv) <= tolerance
            elif field == 'disabled':
                equal = type(av) is bool and type(bv) is bool and av == bv
            elif field in ('width', 'height', 'x', 'y', 'transform'):
                equal = type(av) is int and type(bv) is int and av == bv
            else:
                equal = isinstance(av, str) and isinstance(bv, str) and av == bv
            if not equal:
                differences.append({'output': name, 'field': field, 'expected': av, 'observed': bv})
    return {'schemaVersion': 1, 'mode': 'read-only-verification', 'equivalentObservableState': not differences,
            'physicalOutputsChangedByVerifier': False, 'persistentConfigChanged': False,
            'tolerances': {'refreshRateHz': hz_tolerance, 'scale': scale_tolerance},
            'checkedOutputs': sorted(set(before) | set(after)), 'differences': differences,
            'limitations': ['Does not verify VRR or other unobservable flags',
                            'Does not prove generated Hyprland commands are valid',
                            'Does not perform or guarantee physical rollback']}

def selftest():
    base = [{'name': 'eDP-2', 'width': 1920, 'height': 1080, 'x': 0, 'y': 0, 'scale': 1.0,
             'transform': 0, 'disabled': False, 'mirrorOf': 'none', 'refreshRate': 60.061}]
    assert compare(base, [dict(base[0], refreshRate=60.06)])['equivalentObservableState']
    assert not compare(base, [dict(base[0], x=1920)])['equivalentObservableState']
    assert not compare(base, [])['equivalentObservableState']
    assert not compare(base, [dict(base[0], mirrorOf='HDMI-A-1')])['equivalentObservableState']
    assert not compare(base, [dict(base[0], refreshRate=59.0)])['equivalentObservableState']
    try:
        indexed(base + base)
    except ValueError:
        pass
    else:
        raise AssertionError('Duplicate output accepted')
    return {'selftest': 'passed', 'physicalOutputsChanged': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('selftest')
    verify = sub.add_parser('verify')
    verify.add_argument('--snapshot', required=True, type=Path)
    verify.add_argument('--current', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'selftest':
        print(json.dumps(selftest(), indent=2))
        return 0
    result = compare(read(args.snapshot), read(args.current))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['equivalentObservableState'] else 2

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({'error': str(error), 'equivalentObservableState': False}), file=sys.stderr)
        sys.exit(1)
