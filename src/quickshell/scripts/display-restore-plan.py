#!/usr/bin/env python3
"""Serpantinum v0.5.4 restoration planner. DRY RUN ONLY.

Reads a v0.5 snapshot and optionally a current snapshot, validates them,
then emits structured monitor-rule arguments. NEVER executes hyprctl.
This cannot recover unobserved monitor flags (e.g. VRR) or disconnected outputs.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

NAME = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
MODE = re.compile(r'^(\d{2,5})x(\d{2,5})@(\d+(?:\.\d+)?)(?:Hz)?$', re.I)


def read_snapshot(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Snapshot exceeds 1 MiB')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or not isinstance(data.get('outputs'), list):
        raise ValueError('Expected v0.5 snapshot with outputs list')
    return data['outputs']


def validate(outputs):
    seen = set()
    normalized = []
    for item in outputs:
        if not isinstance(item, dict):
            raise ValueError('Malformed output')
        name = item.get('name')
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
            raise ValueError('Invalid or duplicate output name')
        seen.add(name)
        disabled = item.get('disabled')
        if type(disabled) is not bool:
            raise ValueError('Invalid disabled field')
        mirror = item.get('mirrorOf', 'none')
        if not isinstance(mirror, str) or (mirror != 'none' and not NAME.fullmatch(mirror)):
            raise ValueError('Invalid mirror target')
        if mirror == name:
            raise ValueError('Output cannot mirror itself')
        values = {}
        for key in ('width', 'height', 'x', 'y', 'transform'):
            v = item.get(key)
            if type(v) is not int or abs(v) > 100000:
                raise ValueError('Invalid ' + key + ' for ' + name)
            values[key] = v
        if values['transform'] not in range(8):
            raise ValueError('Invalid transform')
        if not disabled and (values['width'] < 1 or values['height'] < 1):
            raise ValueError('Enabled output has invalid dimensions')
        for key in ('scale', 'refreshRate'):
            v = item.get(key)
            if type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError('Invalid ' + key)
            values[key] = float(v)
        if not disabled and (not .5 <= values['scale'] <= 4 or not 1 <= values['refreshRate'] <= 1000):
            raise ValueError('Invalid scale or refresh rate')
        modes = item.get('availableModes')
        if not isinstance(modes, list) or not all(isinstance(m, str) for m in modes):
            raise ValueError('Invalid availableModes')
        normalized.append(dict(name=name, disabled=disabled, mirrorOf=mirror, availableModes=modes, **values))
    if not normalized:
        raise ValueError('Empty snapshot')
    byname = {m['name']: m for m in normalized}
    if not any(not m['disabled'] for m in normalized):
        raise ValueError('Snapshot would leave no enabled outputs')
    for m in normalized:
        target = m['mirrorOf']
        if target != 'none':
            if target not in byname or byname[target]['disabled'] or m['disabled']:
                raise ValueError('Missing or disabled mirror source')
            if byname[target]['mirrorOf'] != 'none':
                raise ValueError('Nested mirror relationships unsupported')
    return normalized


def plan(saved, current):
    saved = validate(saved)
    current = validate(current)
    present = {m['name'] for m in current}
    missing = sorted(m['name'] for m in saved if m['name'] not in present)
    if missing:
        raise ValueError('Fail closed: saved outputs missing from current snapshot: ' + ', '.join(missing))
    saved_by_name = {m['name']: m for m in saved}
    # Restore independent sources first, mirrors second, disabled last.
    ordered = sorted(saved, key=lambda m: (2 if m['disabled'] else 1 if m['mirrorOf'] != 'none' else 0, m['name']))
    actions = []
    for m in ordered:
        if m['disabled']:
            # Disable operations are intentionally withheld until real rollback is proven.
            raise ValueError('Fail closed: disabled-output restoration is not supported in v0.5.4')
        mode = f"{m['width']}x{m['height']}@{m['refreshRate']:.2f}"
        # Mode rounding from compositor can vary; require advertised near match.
        if not any((match := MODE.fullmatch(raw)) and int(match[1]) == m['width'] and int(match[2]) == m['height'] and abs(float(match[3]) - m['refreshRate']) < .02 for raw in m['availableModes']):
            raise ValueError('Saved mode not advertised for ' + m['name'])
        rule = f"{m['name']},{mode},{m['x']}x{m['y']},{m['scale']:g},transform,{m['transform']}"
        if m['mirrorOf'] != 'none':
            rule += ',mirror,' + m['mirrorOf']
        actions.append({'output': m['name'], 'argv': ['hyprctl', 'keyword', 'monitor', rule], 'previewOnly': True})
    return {'schemaVersion': 1, 'mode': 'dry-run-only', 'safeToExecuteAutomatically': False,
            'persistentConfigChanged': False, 'physicalOutputsChanged': False,
            'limitations': ['No commands executed', 'VRR and other non-observable flags not restored',
                            'Disconnected outputs and disabled-output restoration fail closed',
                            'Hyprland command syntax must be verified before real execution'],
            'actions': actions}


def selftest():
    a = {'name':'eDP-2','disabled':False,'mirrorOf':'none','width':1920,'height':1080,
         'refreshRate':60.061,'x':0,'y':0,'scale':1,'transform':0,
         'availableModes':['1920x1080@60.06Hz','1920x1080@144.06Hz']}
    b = dict(a, name='HDMI-A-1', x=1920, refreshRate=60.0, availableModes=['1920x1080@60.00Hz'])
    result = plan([a,b],[a,b])
    assert len(result['actions']) == 2 and result['physicalOutputsChanged'] is False
    def rejects(saved, current):
        try: plan(saved, current)
        except ValueError: return True
        return False
    assert rejects([a,b],[a])
    assert rejects([dict(a,transform=8),b],[a,b])
    assert rejects([dict(a,availableModes=[]),b],[a,b])
    assert rejects([dict(a,mirrorOf='eDP-2'),b],[a,b])
    assert rejects([dict(a,disabled=True),b],[a,b])
    return {'version':'0.5.4','tests':'passed','physicalOutputsChanged':False,'commandsExecuted':0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['selftest','plan'])
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--current', type=Path)
    args = parser.parse_args()
    if args.action == 'selftest':
        output = selftest()
    else:
        if not args.snapshot or not args.current:
            parser.error('plan requires --snapshot and --current')
        output = plan(read_snapshot(args.snapshot), read_snapshot(args.current))
    print(json.dumps(output, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
