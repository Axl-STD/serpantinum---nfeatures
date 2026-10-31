#!/usr/bin/env python3
"""Serpantinum 0.5.9: read-only guard for proposed monitor commands.

Never executes hyprctl, changes output state, or edits configuration. Structural
validation is NOT a proof of Hyprland 0.56.2 grammar compatibility.
"""
import argparse
import json
import re
import sys
from pathlib import Path

NAME = re.compile(r'[A-Za-z0-9_.:-]{1,100}\Z')
MODE = re.compile(r'([1-9][0-9]{1,4})x([1-9][0-9]{1,4})@([0-9]+(?:\.[0-9]+)?)\Z')
POSITION = re.compile(r'(-?[0-9]{1,6})x(-?[0-9]{1,6})\Z')
SCALE = re.compile(r'(?:[0-9]+)(?:\.[0-9]+)?\Z')
ALLOWED = {'transform', 'vrr', 'mirror'}


def inspect_action(action):
    if not isinstance(action, dict) or action.get('previewOnly') is not True:
        raise ValueError('Action must be previewOnly')
    argv = action.get('argv')
    if not isinstance(argv, list) or len(argv) != 4 or argv[:3] != ['hyprctl', 'keyword', 'monitor']:
        raise ValueError('Unexpected executable or command arguments')
    if not isinstance(argv[3], str) or len(argv[3]) > 512:
        raise ValueError('Invalid rule payload')
    tokens = argv[3].split(',')
    if len(tokens) < 4 or len(tokens) % 2:
        raise ValueError('Malformed monitor rule')
    name, mode, pos, scale = tokens[:4]
    if not NAME.fullmatch(name) or name != action.get('output'):
        raise ValueError('Monitor name mismatch')
    match = MODE.fullmatch(mode)
    if not match or not 1 <= float(match[3]) <= 1000:
        raise ValueError('Unsupported mode')
    if not POSITION.fullmatch(pos):
        raise ValueError('Unsupported position')
    if not SCALE.fullmatch(scale) or not 0.5 <= float(scale) <= 4:
        raise ValueError('Unsupported scale')
    opts = tokens[4:]
    seen = set()
    parsed = {}
    for i in range(0, len(opts), 2):
        key, val = opts[i:i+2]
        if key not in ALLOWED or key in seen:
            raise ValueError('Unknown or duplicate option')
        seen.add(key)
        if key == 'transform' and val not in [str(x) for x in range(8)]:
            raise ValueError('Invalid transform')
        if key == 'vrr' and val not in ('0', '1', '2', '3'):
            raise ValueError('Invalid VRR')
        if key == 'mirror' and (not NAME.fullmatch(val) or val == name):
            raise ValueError('Invalid mirror target')
        parsed[key] = val
    if 'transform' not in parsed:
        raise ValueError('Missing transform')
    return {'output': name, 'mode': mode, 'position': pos, 'scale': float(scale), 'options': parsed}


def inspect_plan(plan):
    if not isinstance(plan, dict) or plan.get('safeToExecuteAutomatically') is not False or plan.get('mode') != 'dry-run-only':
        raise ValueError('Plan must explicitly prohibit execution')
    actions = plan.get('actions')
    if not isinstance(actions, list) or not actions or len(actions) > 16:
        raise ValueError('Invalid action count')
    inspected = [inspect_action(a) for a in actions]
    names = [a['output'] for a in inspected]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate outputs')
    for item in inspected:
        mirror = item['options'].get('mirror')
        if mirror and (mirror not in names or next(a for a in inspected if a['output'] == mirror)['options'].get('mirror')):
            raise ValueError('Unknown or nested mirror source')
    return {'schemaVersion': 1, 'version': '0.5.9', 'mode': 'structural-audit-only',
            'structurallyValid': True, 'hyprlandSyntaxVerified': False,
            'safeToExecuteAutomatically': False, 'commandsExecuted': 0,
            'physicalOutputsChanged': False, 'persistentConfigChanged': False,
            'outputs': inspected,
            'limitations': ['No compositor grammar validation', 'No physical apply or rollback',
                            'Runtime VRR not verified', 'Do not use this output as an execution authorization']}


def selftest():
    good = {'mode': 'dry-run-only', 'safeToExecuteAutomatically': False,
            'actions': [{'output': 'eDP-2', 'previewOnly': True, 'argv': ['hyprctl', 'keyword', 'monitor', 'eDP-2,1920x1080@60.06,0x0,1,transform,0,vrr,0']}]}
    assert inspect_plan(good)['structurallyValid']
    for payload in ['eDP-2,1920x1080@60.06,0x0,1,transform,0,exec,evil',
                    'eDP-2,1920x1080@60.06,0x0,1,transform,8',
                    'eDP-2,1920x1080@60.06,auto,1,transform,0',
                    'eDP-2,1920x1080@60.06,0x0,1,mirror,eDP-2',
                    'eDP-2,1920x1080@60.06,0x0,1,transform,0,vrr,0,vrr,1']:
        altered = json.loads(json.dumps(good))
        altered['actions'][0]['argv'][3] = payload
        try:
            inspect_plan(altered)
        except ValueError:
            continue
        raise AssertionError('Unsafe rule accepted: ' + payload)
    return {'selftest': 'passed', 'commandsExecuted': 0, 'physicalOutputsChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['selftest', 'inspect'])
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'selftest':
            result = selftest()
        else:
            if args.plan is None or not args.plan.is_file() or args.plan.stat().st_size > 1024 * 1024:
                raise ValueError('Missing or oversized plan')
            result = inspect_plan(json.loads(args.plan.read_text(encoding='utf-8')))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
