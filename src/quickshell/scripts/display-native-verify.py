#!/usr/bin/env python3
"""Serpantinum v0.5.10: structural and native config parser verification only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def inspect(plan):
    guard_path = Path(__file__).with_name('display-executor-guard.py')
    spec = importlib.util.spec_from_file_location('display_executor_guard', guard_path)
    if spec is None or spec.loader is None:
        raise ValueError('Structural guard unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inspect_plan(plan)


def verify(plan, executable='Hyprland'):
    structural = inspect(plan)
    rules = [item['argv'][3] for item in plan['actions']]
    if len(rules) != len(structural['outputs']):
        raise ValueError('Structural inspection mismatch')
    with tempfile.TemporaryDirectory(prefix='serpantinum-display-verify-') as folder:
        config = Path(folder) / 'monitors.conf'
        config.write_text(''.join('monitor=' + rule + '\n' for rule in rules), encoding='utf-8')
        os.chmod(config, 0o600)
        try:
            proc = subprocess.run([executable, '--verify-config', '--config', str(config)],
                                  capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('Native verification could not complete: ' + str(exc)) from exc
    accepted = proc.returncode == 0 and 'config ok' in (proc.stdout + proc.stderr).lower()
    return {
        'schemaVersion': 1, 'version': '0.5.10', 'mode': 'read-only-native-validation',
        'structurallyValid': True, 'nativeConfigParserAccepted': accepted,
        'hyprlandSyntaxVerified': accepted, 'safeToExecuteAutomatically': False,
        'commandsExecuted': 0, 'physicalOutputsChanged': False,
        'persistentConfigChanged': False,
        'outputs': [item['output'] for item in structural['outputs']],
        'nativeExitCode': proc.returncode,
        'nativeOutput': (proc.stdout + proc.stderr)[-3000:],
        'limitations': ['Native parser acceptance does not guarantee hardware mode support',
                        'No physical apply or rollback', 'No runtime VRR verification']
    }


def selftest():
    good = {'mode': 'dry-run-only', 'safeToExecuteAutomatically': False,
            'actions': [{'output': 'eDP-2', 'previewOnly': True,
                         'argv': ['hyprctl', 'keyword', 'monitor',
                                  'eDP-2,1920x1080@60.06,0x0,1,transform,0,vrr,0']}]}
    assert inspect(good)['structurallyValid']
    bad = json.loads(json.dumps(good))
    bad['actions'][0]['argv'][3] += ',exec,unsafe'
    try:
        inspect(bad)
    except ValueError:
        pass
    else:
        raise AssertionError('Unsafe command accepted')
    return {'selftest': 'passed', 'nativeIntegration': 'not-run',
            'commandsExecuted': 0, 'physicalOutputsChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['selftest', 'verify'])
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'selftest':
            result = selftest()
        else:
            if args.plan is None or not args.plan.is_file() or args.plan.stat().st_size > 1024 * 1024:
                raise ValueError('Missing or oversized plan')
            result = verify(json.loads(args.plan.read_text(encoding='utf-8')))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if args.action == 'selftest' or result['nativeConfigParserAccepted'] else 2
    except (ValueError, OSError, json.JSONDecodeError, AssertionError) as exc:
        print(json.dumps({'error': str(exc), 'safeToExecuteAutomatically': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
