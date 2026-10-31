#!/usr/bin/env python3
"""v0.5.5: audit a dry-run monitor restoration plan. NEVER executes commands.

Read-only inspection of an optional Hyprland monitor configuration. Fail closed
on unknown flags, wildcard rules, duplicate rules, or mismatched output names.
No persistent configuration is modified.
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN_PATH = HERE / 'display-restore-plan.py'
NAME = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
# Only flag pairs whose semantics are explicitly retained in this prototype.
# Other options require review before any physical rollback.
SUPPORTED = {'vrr': {'0', '1', '2', '3'}}


def load_planner():
    spec = importlib.util.spec_from_file_location('display_restore_plan', PLAN_PATH)
    if spec is None or spec.loader is None:
        raise ValueError('Missing v0.5.4 planner')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_rules(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Configuration exceeds 1 MiB')
    result = {}
    for line_number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if not line.startswith('monitor=') and not line.startswith('monitor ='):
            continue
        payload = line.split('=', 1)[1].strip()
        tokens = [part.strip() for part in payload.split(',')]
        if len(tokens) < 4:
            raise ValueError(f'Malformed monitor rule at line {line_number}')
        name = tokens[0]
        if not NAME.fullmatch(name) or name in result:
            raise ValueError(f'Unsupported wildcard, duplicate, or invalid output at line {line_number}')
        extras = tokens[4:]
        if len(extras) % 2:
            raise ValueError(f'Unpaired monitor option at line {line_number}')
        flags = []
        for index in range(0, len(extras), 2):
            key, value = extras[index:index+2]
            if key not in SUPPORTED or value not in SUPPORTED[key]:
                raise ValueError(f'Unsupported option {key}={value} for {name}; manual review required')
            if any(flag[0] == key for flag in flags):
                raise ValueError(f'Duplicate option {key} for {name}')
            flags.append((key, value))
        result[name] = {'rawRule': payload, 'options': flags, 'sourceLine': line_number}
    return result


def audit(saved, current, config):
    planner = load_planner()
    base = planner.plan(saved, current)
    saved_names = {output['name'] for output in saved}
    rules = parse_rules(config)
    if set(rules) != saved_names:
        raise ValueError('Monitor rules do not exactly match snapshot outputs; fail closed')
    # Do not assume the persistent rule matches the live snapshot: report it.
    # VRR can be included in a preview, but it is NOT a proven rollback.
    actions = []
    for action in base['actions']:
        name = action['output']
        options = rules[name]['options']
        args = list(action['argv'])
        if options:
            args[3] += ',' + ','.join(part for pair in options for part in pair)
        actions.append({'output': name, 'argv': args, 'previewOnly': True,
                        'preservedConfigOptions': [{'key': k, 'value': v} for k, v in options],
                        'originalPersistentRule': rules[name]['rawRule']})
    return {'schemaVersion': 1, 'version': '0.5.5', 'mode': 'dry-run-only',
            'safeToExecuteAutomatically': False, 'physicalOutputsChanged': False,
            'persistentConfigChanged': False, 'configPath': str(config),
            'limitations': ['Commands not executed or syntax verified against compositor',
                            'Runtime state of VRR and unobservable flags not verified',
                            'No support for wildcard, layered, disabled, or unknown monitor rules',
                            'Persistent rules may differ from runtime state',
                            'No equivalence proof or physical rollback yet'],
            'actions': actions}


def selftest():
    import tempfile
    planner = load_planner()
    a = {'name': 'eDP-2', 'disabled': False, 'mirrorOf': 'none', 'width': 1920,
         'height': 1080, 'refreshRate': 60.061, 'x': 0, 'y': 0, 'scale': 1,
         'transform': 0, 'availableModes': ['1920x1080@60.06Hz']}
    b = dict(a, name='HDMI-A-1', x=1920, refreshRate=60.0,
             availableModes=['1920x1080@60.00Hz'])
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'monitors.conf'
        path.write_text('monitor=HDMI-A-1,1920x1080@60.0,1920x0,1.0,vrr,0\n'
                        'monitor=eDP-2,1920x1080@60.06,0x0,1,vrr,0\n')
        result = audit([a,b], [a,b], path)
        assert len(result['actions']) == 2
        assert all(action['preservedConfigOptions'] == [{'key':'vrr','value':'0'}] for action in result['actions'])
        assert result['safeToExecuteAutomatically'] is False
        for bad in ['monitor=,preferred,auto,1\n',
                    'monitor=eDP-2,1920x1080@60,0x0,1,unknown,1\n',
                    'monitor=eDP-2,1920x1080@60,0x0,1\nmonitor=eDP-2,1920x1080@60,0x0,1\n']:
            path.write_text(bad)
            try:
                audit([a,b], [a,b], path)
            except ValueError:
                continue
            raise AssertionError('Expected fail-closed behavior')
    return {'version':'0.5.5', 'tests':'passed', 'commandsExecuted':0,
            'physicalOutputsChanged':False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['selftest', 'audit'])
    p.add_argument('--snapshot', type=Path)
    p.add_argument('--current', type=Path)
    p.add_argument('--config', type=Path)
    args = p.parse_args()
    if args.action == 'selftest':
        result = selftest()
    else:
        if not all((args.snapshot, args.current, args.config)):
            p.error('audit requires --snapshot, --current and --config')
        planner = load_planner()
        result = audit(planner.read_snapshot(args.snapshot),
                       planner.read_snapshot(args.current), args.config)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
