#!/usr/bin/env python3
"""Serpantinum v0.5.34: isolated systemd worker interruption tests.

Test-only: never changes monitors, the live bundle, or persistent Hyprland config.
A crashed rollback-running worker is deliberately NOT retried or certified.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
TERMINAL = {'rollback-rehearsal-verified', 'rollback-rehearsal-failed', 'rollback-rehearsal-incomplete'}


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(root, phase):
    persist = load('display-recovery-persistence.py', 'persist_setup')
    token = secrets.token_hex(12)
    now = time.time()
    persist.init(root, token, now + 1.0)
    persist.advance(root, token, 'supervisor-ready', now)
    persist.advance(root, token, 'reserve', now)
    persist.advance(root, token, 'initiator-exited', now)
    if phase in ('deadline-expired', 'rollback-running', 'terminal'):
        persist.advance(root, token, 'expire', now + 2)
    if phase in ('rollback-running', 'terminal'):
        persist.advance(root, token, 'recover', now + 2)
    if phase == 'terminal':
        persist.finish(root, token, 'verified')
    return token


def crash_worker(root, token, where):
    persist = load('display-recovery-persistence.py', 'persist_crash')
    state = persist.status(root)['state']
    if state['transactionId'] != token:
        raise ValueError('Wrong identity')
    if where == 'before-recover':
        if state['phase'] != 'deadline-expired':
            raise ValueError('Unexpected pre-recovery phase')
        os._exit(71)
    if where == 'after-recover':
        if state['phase'] != 'deadline-expired':
            raise ValueError('Unexpected pre-recovery phase')
        persist.advance(root, token, 'recover', time.time())
        os._exit(72)
    raise ValueError('Unknown crash point')


def start_unit(root, token, mode, suffix):
    unit = 'serpantinum-v0534-' + token + '-' + suffix
    cmd = ['systemd-run', '--user', '--collect', '--quiet', '--unit=' + unit,
           sys.executable, str(Path(__file__).resolve()), '_crash', '--root', str(root),
           '--token', token, '--where', mode]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('systemd-run failed: ' + result.stderr[:250])
    return unit + '.service'


def wait_inactive(unit):
    for _ in range(100):
        result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--no-pager'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and 'ActiveState=failed' in result.stdout:
            return
        if result.returncode == 0 and 'ActiveState=inactive' in result.stdout:
            return
        time.sleep(.1)
    raise RuntimeError('Unit did not terminate: ' + unit)


def assert_safe(state, expected):
    if (state['phase'] != expected or state.get('physicalExecutionEnabled') is not False or
            state.get('physicalRollbackVerified') is not False or state.get('commandsExecuted') != 0):
        raise AssertionError('Unsafe or unexpected journal state: ' + repr(state))


def run():
    persist = load('display-recovery-persistence.py', 'persist_run')
    worker = load('display-persistent-systemd-worker.py', 'worker_run')
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0534-', dir=os.environ.get('XDG_RUNTIME_DIR')) as temp:
        os.chmod(temp, 0o700)
        base = Path(temp)
        for mode, expected in [('before-recover', 'deadline-expired'), ('after-recover', 'rollback-running')]:
            root = base / mode
            root.mkdir(mode=0o700)
            token = setup(root, 'deadline-expired')
            unit = start_unit(root, token, mode, mode.replace('-', ''))
            wait_inactive(unit)
            state = persist.status(root)['state']
            assert_safe(state, expected)
            if mode == 'before-recover':
                result = worker.worker(root, token, 'verified', clock=lambda: time.time())
                if result['phase'] != 'rollback-rehearsal-verified':
                    raise AssertionError('Restart before recovery did not finish')
                assert_safe(persist.status(root)['state'], 'rollback-rehearsal-verified')
                results.append({'scenario': mode, 'resumed': True, 'phase': 'rollback-rehearsal-verified'})
            else:
                try:
                    worker.worker(root, token, 'verified')
                except ValueError as exc:
                    if 'already running' not in str(exc):
                        raise
                else:
                    raise AssertionError('Unsafe replay of abandoned rollback-running')
                assert_safe(persist.status(root)['state'], 'rollback-running')
                results.append({'scenario': mode, 'resumed': False, 'phase': 'rollback-running', 'manualInvestigationRequired': True})
        root = base / 'duplicate'
        root.mkdir(mode=0o700)
        token = setup(root, 'deadline-expired')
        # Two independent worker processes race to advance and finish the same journal.
        cmd = [sys.executable, str(HERE / 'display-persistent-systemd-worker.py'), '_worker',
               '--root', str(root), '--token', token, '--scenario', 'verified']
        a = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        b = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out_a, err_a = a.communicate(timeout=15)
        out_b, err_b = b.communicate(timeout=15)
        state = persist.status(root)['state']
        assert_safe(state, 'rollback-rehearsal-verified')
        if a.returncode != 0 and b.returncode != 0:
            raise AssertionError('Neither worker completed')
        if state['history'].count('rollback-rehearsal-verified') != 1:
            raise AssertionError('Duplicate terminal transition')
        results.append({'scenario': 'concurrent-workers', 'terminalWrites': 1, 'phase': state['phase'],
                        'workerExitCodes': [a.returncode, b.returncode]})
        root = base / 'terminal'
        root.mkdir(mode=0o700)
        token = setup(root, 'terminal')
        before = persist.status(root)
        result = worker.worker(root, token, 'operation-failure')
        if not result.get('alreadyTerminal') or persist.status(root) != before:
            raise AssertionError('Terminal journal overwritten')
        results.append({'scenario': 'terminal-revisit', 'unchanged': True, 'phase': before['state']['phase']})
    return {'version': '0.5.34', 'mode': 'isolated-systemd-interruption-tests', 'scenarios': results,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False, 'commandsExecuted': 0}


def selftest():
    persist = load('display-recovery-persistence.py', 'persist_selftest')
    worker = load('display-persistent-systemd-worker.py', 'worker_selftest')
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0534-selftest-') as root:
        os.chmod(root, 0o700)
        token = setup(root, 'rollback-running')
        try:
            worker.worker(root, token, 'verified')
        except ValueError:
            pass
        else:
            raise AssertionError('Abandoned running worker accepted')
        assert_safe(persist.status(root)['state'], 'rollback-running')
    return {'selftest': 'passed', 'abandonedRecoveryRejected': True,
            'systemdIntegration': 'not-run', 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest', 'run', '_crash'])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--token')
    parser.add_argument('--where', choices=['before-recover', 'after-recover'])
    args = parser.parse_args()
    if args.command == '_crash':
        if args.root is None or args.token is None or args.where is None:
            raise ValueError('Missing crash worker arguments')
        crash_worker(args.root, args.token, args.where)
        return 2
    result = selftest() if args.command == 'selftest' else run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
