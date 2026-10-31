#!/usr/bin/env python3
"""Serpantinum display transaction prototype: strictly read-only dry run.

No command in this prototype changes Hyprland outputs or writes monitors.conf.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

NAME = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
MODE = re.compile(r'^(\d{2,5})x(\d{2,5})@(\d+(?:\.\d+)?)Hz?$', re.I)


def monitors():
    p = subprocess.run(['hyprctl', 'monitors', 'all', '-j'], capture_output=True, text=True, timeout=8, check=True)
    data = json.loads(p.stdout)
    if not isinstance(data, list):
        raise ValueError('Hyprland returned an unexpected monitor collection')
    return data


def normalize(data):
    result = []
    for item in data:
        name = item.get('name')
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('Invalid monitor name in compositor response')
        modes = item.get('availableModes') or []
        if not isinstance(modes, list):
            raise ValueError('Unexpected availableModes type')
        result.append({
            'name': name,
            'description': item.get('description') or name,
            'width': item.get('width'), 'height': item.get('height'),
            'refreshRate': item.get('refreshRate'),
            'x': item.get('x'), 'y': item.get('y'),
            'scale': item.get('scale'), 'transform': item.get('transform'),
            'disabled': item.get('disabled', False),
            'mirrorOf': item.get('mirrorOf', 'none'),
            'availableModes': modes,
        })
    return result


def validate_plan(plan, state):
    if not isinstance(plan, dict) or set(plan) != {'outputs'} or not isinstance(plan['outputs'], list):
        raise ValueError('Plan must be an object with an outputs array only')
    current = {m['name']: m for m in state}
    seen = set()
    changes = []
    for entry in plan['outputs']:
        if not isinstance(entry, dict) or set(entry) - {'name', 'mode', 'transform', 'scale', 'x', 'y'}:
            raise ValueError('Unknown or malformed output setting')
        name = entry.get('name')
        if name not in current or name in seen:
            raise ValueError('Unknown or duplicate monitor')
        seen.add(name)
        before = current[name]
        proposed = {'name': name}
        if 'mode' in entry:
            value = entry['mode']
            if not isinstance(value, str) or not MODE.fullmatch(value):
                raise ValueError('Invalid mode syntax')
            match = MODE.fullmatch(value)
            width, height, hz = int(match[1]), int(match[2]), float(match[3])
            if not any((m := MODE.fullmatch(str(raw))) and int(m[1]) == width and int(m[2]) == height and abs(float(m[3]) - hz) < .015 for raw in before['availableModes']):
                raise ValueError('Mode not advertised for ' + name)
            proposed['mode'] = value
        if 'transform' in entry:
            value = entry['transform']
            if type(value) is not int or value not in range(8):
                raise ValueError('Transform must be an integer from 0 to 7')
            proposed['transform'] = value
        if 'scale' in entry:
            value = entry['scale']
            if type(value) not in (int, float) or not 0.5 <= value <= 4:
                raise ValueError('Scale outside supported validation range')
            proposed['scale'] = value
        for key in ('x', 'y'):
            if key in entry:
                value = entry[key]
                if type(value) is not int or abs(value) > 100000:
                    raise ValueError('Invalid coordinate')
                proposed[key] = value
        changes.append({'before': before, 'proposed': proposed})
    if not changes:
        raise ValueError('Empty plan')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['snapshot', 'prepare', 'status'])
    parser.add_argument('--plan', type=Path, help='JSON plan file, used only for prepare')
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps({'phase': 'dry-run', 'pending': False, 'applyEnabled': False, 'rollbackEnabled': False}, indent=2))
        return
    state = normalize(monitors())
    if args.action == 'snapshot':
        print(json.dumps({'schemaVersion': 1, 'source': 'hyprctl monitors all -j', 'persistentConfigChanged': False, 'outputs': state}, indent=2, ensure_ascii=False))
    else:
        if not args.plan:
            parser.error('prepare requires --plan')
        if args.plan.stat().st_size > 65536:
            raise ValueError('Plan exceeds 64 KiB')
        plan = json.loads(args.plan.read_text(encoding='utf-8'))
        changes = validate_plan(plan, state)
        print(json.dumps({'valid': True, 'dryRun': True, 'applyEnabled': False, 'changes': changes}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
