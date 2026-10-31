#!/usr/bin/env python3
"""Serpantinum v0.5.33: isolated systemd worker for simulated persistent recovery.

Never runs compositor commands, touches the live bundle, or enables physical recovery.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
SCENARIOS = ('verified', 'operation-failure', 'incomplete', 'observer-failure')
TERMINAL = ('rollback-rehearsal-verified', 'rollback-rehearsal-incomplete', 'rollback-rehearsal-failed')


def persistence():
    spec = importlib.util.spec_from_file_location('display_persistence_0533', HERE / 'display-recovery-persistence.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate(root, token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    mod = persistence()
    mod.directory(root)
    state = mod.status(root)['state']
    if state.get('transactionId') != token:
        raise ValueError('Transaction mismatch')
    if state.get('physicalExecutionEnabled') is not False or state.get('commandsExecuted') != 0:
        raise ValueError('Physical execution forbidden')
    return mod, state


def worker(root, token, scenario, clock=time.time, sleeper=time.sleep):
    if scenario not in SCENARIOS:
        raise ValueError('Unknown scenario')
    mod, state = validate(root, token)
    while True:
        mod, state = validate(root, token)
        phase = state['phase']
        if phase in TERMINAL:
            return {'phase': phase, 'alreadyTerminal': True, 'physicalRollbackVerified': False}
        if phase == 'rollback-running':
            # Never infer success after a crashed worker: fail closed.
            raise ValueError('Recovery already running; manual investigation required')
        if phase == 'deadline-expired':
            mod.advance(root, token, 'recover', clock())
            break
        if phase not in ('supervised', 'operation-reserved', 'operation-interrupted'):
            raise ValueError('Transaction not supervised')
        remaining = state['deadline'] - clock()
        if remaining <= 0:
            try:
                mod.advance(root, token, 'expire', clock())
            except ValueError:
                continue  # Another worker may have advanced it; re-read.
        else:
            sleeper(min(remaining, .25))
    result = mod.finish(root, token, scenario)
    final = result['state']
    return {'phase': final['phase'], 'alreadyTerminal': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def run_isolated():
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0533-', dir=os.environ.get('XDG_RUNTIME_DIR')) as base:
        os.chmod(base, 0o700)
        mod = persistence()
        for i, scenario in enumerate(SCENARIOS):
            root = Path(base) / scenario
            root.mkdir(mode=0o700)
            token = secrets.token_hex(12)
            deadline = time.time() + 2.0
            mod.init(root, token, deadline)
            mod.advance(root, token, 'supervisor-ready', time.time())
            mod.advance(root, token, 'reserve', time.time())
            mod.advance(root, token, 'initiator-exited', time.time())
            unit = 'serpantinum-v0533-' + token
            command = ['systemd-run', '--user', '--collect', '--quiet', '--unit=' + unit,
                       sys.executable, str(Path(__file__).resolve()), '_worker', '--root', str(root),
                       '--token', token, '--scenario', scenario]
            launch = subprocess.run(command, capture_output=True, text=True, timeout=10)
            if launch.returncode:
                raise RuntimeError('systemd launch failed: ' + launch.stderr[:300])
            expected = 'rollback-rehearsal-verified' if scenario == 'verified' else ('rollback-rehearsal-incomplete' if scenario == 'incomplete' else 'rollback-rehearsal-failed')
            end = time.monotonic() + 12
            while time.monotonic() < end:
                state = mod.status(root)['state']
                if state['phase'] in TERMINAL:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Worker did not finish: ' + scenario)
            if state['phase'] != expected or state.get('physicalRollbackVerified') is not False or state.get('commandsExecuted') != 0:
                raise RuntimeError('Unexpected terminal state: ' + scenario)
            # Wait for unit to stop before TemporaryDirectory removes the root.
            for _ in range(50):
                check = subprocess.run(['systemctl', '--user', 'is-active', unit + '.service'], capture_output=True, text=True, timeout=5)
                if check.stdout.strip() != 'active':
                    break
                time.sleep(.1)
            results.append({'scenario': scenario, 'phase': state['phase'], 'commandsExecuted': 0})
    return {'version': '0.5.33', 'mode': 'isolated-systemd-simulation', 'scenarios': results,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False, 'commandsExecuted': 0}


def selftest():
    mod = persistence()
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0533-selftest-') as root:
        os.chmod(root, 0o700)
        token = 'a' * 24
        mod.init(root, token, 15)
        mod.advance(root, token, 'supervisor-ready', 1)
        mod.advance(root, token, 'reserve', 2)
        mod.advance(root, token, 'initiator-exited', 3)
        result = worker(root, token, 'verified', clock=lambda: 16, sleeper=lambda _: None)
        assert result['phase'] == 'rollback-rehearsal-verified'
        assert worker(root, token, 'verified', clock=lambda: 16)['alreadyTerminal'] is True
        try:
            worker(root, 'b' * 24, 'verified')
            raise AssertionError('Wrong identity accepted')
        except ValueError:
            pass
        assert mod.status(root)['state']['commandsExecuted'] == 0
    return {'selftest': 'passed', 'physicalExecutionEnabled': False,
            'systemdIntegration': 'not-run', 'commandsExecuted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'run', '_worker'])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--token')
    parser.add_argument('--scenario', choices=SCENARIOS)
    args = parser.parse_args()
    if args.command == 'selftest':
        result = selftest()
    elif args.command == 'run':
        result = run_isolated()
    else:
        if args.root is None or args.token is None or args.scenario is None:
            raise ValueError('Missing worker arguments')
        result = worker(args.root, args.token, args.scenario)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
