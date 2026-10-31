#!/usr/bin/env python3
"""Serpantinum v0.5.11: fail-closed, read-only display transaction preflight.

Checks live snapshots, restoration plan, structural rules, and native Hyprland
parser. Optional simulation starts the existing v0.5.8 watchdog AFTER checks.
NEVER applies monitor rules or changes persistent configuration.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def load(filename, module_name):
    path = HERE / filename
    if not path.is_file():
        raise ValueError('Missing dependency: ' + filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ValueError('Cannot load dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture():
    p = subprocess.run([sys.executable, str(HERE / 'display-transaction.py'), 'snapshot'],
                       capture_output=True, text=True, timeout=10, check=False)
    if p.returncode:
        raise ValueError('Snapshot failed: ' + p.stderr[-400:])
    data = json.loads(p.stdout)
    outputs = data.get('outputs')
    if not isinstance(outputs, list) or not outputs:
        raise ValueError('Snapshot has no outputs')
    names = [x.get('name') for x in outputs]
    if len(set(names)) != len(names) or any(not n for n in names):
        raise ValueError('Invalid or duplicate output names')
    return data


def preflight(config, snapshot_fn=capture, audit_fn=None, verify_fn=None):
    if not config.is_file() or config.is_symlink():
        raise ValueError('Monitor config missing or symlinked')
    before = snapshot_fn()
    current = snapshot_fn()
    if {x['name'] for x in before['outputs']} != {x['name'] for x in current['outputs']}:
        raise ValueError('Monitor connections changed during preflight')
    if audit_fn is None:
        audit_fn = load('display-restore-audit.py', 'restore_audit').audit
    if verify_fn is None:
        verify_fn = load('display-native-verify.py', 'native_verify').verify
    plan = audit_fn(before['outputs'], current['outputs'], config)
    if plan.get('safeToExecuteAutomatically') is not False:
        raise ValueError('Audit did not explicitly prohibit execution')
    native = verify_fn(plan)
    if not native.get('structurallyValid') or not native.get('nativeConfigParserAccepted'):
        raise ValueError('Structural or native syntax verification failed')
    return {'schemaVersion': 1, 'version': '0.5.11', 'mode': 'preflight-only',
            'phase': 'prepared-read-only', 'outputs': native['outputs'],
            'structurallyValid': True, 'nativeConfigParserAccepted': True,
            'safeToExecuteAutomatically': False, 'commandsExecuted': 0,
            'physicalOutputsChanged': False, 'persistentConfigChanged': False,
            'physicalRollbackVerified': False,
            'limitations': ['No physical apply or rollback',
                            'Native parser acceptance is not hardware validation',
                            'VRR runtime state is not verified',
                            'Simulated watchdog is not a physical recovery guarantee']}


def selftest():
    fake = {'outputs': [{'name': 'eDP-2'}]}
    def audit(saved, current, config):
        return {'safeToExecuteAutomatically': False, 'actions': []}
    def verify(plan):
        return {'structurallyValid': True, 'nativeConfigParserAccepted': True,
                'outputs': ['eDP-2']}
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / 'monitors.conf'
        config.write_text('monitor=eDP-2,1920x1080@60,0x0,1\n')
        result = preflight(config, lambda: fake, audit, verify)
        assert result['phase'] == 'prepared-read-only'
        assert result['safeToExecuteAutomatically'] is False
        for broken in [lambda p: {'structurallyValid': False, 'nativeConfigParserAccepted': True},
                       lambda p: {'structurallyValid': True, 'nativeConfigParserAccepted': False}]:
            try:
                preflight(config, lambda: fake, audit, broken)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid preflight accepted')
    return {'selftest': 'passed', 'failClosed': True, 'commandsExecuted': 0,
            'physicalOutputsChanged': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'prepare', 'simulate'])
    p.add_argument('--config', type=Path, default=Path.home() / '.config/hypr/monitors.conf')
    p.add_argument('--seconds', type=int, default=15)
    args = p.parse_args()
    try:
        if args.command == 'selftest':
            result = selftest()
        else:
            result = preflight(args.config)
            if args.command == 'simulate':
                if not 5 <= args.seconds <= 120:
                    raise ValueError('Simulation seconds must be 5..120')
                watchdog = HERE / 'display-recovery-watchdog.py'
                if not watchdog.is_file():
                    raise ValueError('Missing v0.5.8 watchdog')
                proc = subprocess.run([sys.executable, str(watchdog), 'start', '--seconds', str(args.seconds)],
                                      capture_output=True, text=True, timeout=15, check=False)
                if proc.returncode:
                    raise ValueError('Watchdog start failed: ' + proc.stderr[-400:])
                result['simulation'] = json.loads(proc.stdout)
                result['phase'] = 'simulation-started'
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired, AssertionError) as exc:
        print(json.dumps({'version': '0.5.11', 'error': str(exc),
                          'safeToExecuteAutomatically': False, 'commandsExecuted': 0}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
