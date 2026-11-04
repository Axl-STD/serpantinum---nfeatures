#!/usr/bin/env python3
"""Offline crash-lifecycle regression for supervised coordinator sessions.
Only signals child processes created by this test; no display/systemd calls.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import importlib.util

HERE = Path(__file__).resolve().parent
PHASES = ('prepared', 'applying', 'awaiting_confirmation', 'rollback_required')
SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGKILL)


def load(filename, key):
    source = HERE / filename
    if source.is_symlink() or not source.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def holder(root, phase):
    supervisor = load('display-transition-supervisor-v0600.py', 'supervisor_holder')
    base = supervisor.mod.base
    runtime, journal, broker = (root / name for name in ('runtime', 'journal', 'broker'))
    with supervisor.SupervisedSession(runtime, journal, broker):
        state = base.serialized.guard.prepare(journal, 'offline-probe', 'synthetic-digest')
        if phase != 'prepared':
            state = base.serialized.guard.advance(journal, state['generation'], 'applying')
        if phase in ('awaiting_confirmation', 'rollback_required'):
            state = base.serialized.guard.advance(journal, state['generation'], phase)
        print('READY', flush=True)
        time.sleep(30)


def selftest():
    owner = load('display-admission-owner-v0600.py', 'owner_crash')
    serialized = load('display-transaction-serialized-v0600.py', 'serialized_crash')
    guard = serialized.guard
    rows = []
    def check(label, passed):
        rows.append({'scenario': label, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(label)

    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-crash-') as temporary:
        parent = Path(temporary)
        for phase in PHASES:
            for signum in SIGNALS:
                label = phase + '-' + signal.Signals(signum).name.lower()
                root = parent / label
                root.mkdir(mode=0o700)
                for name in ('runtime', 'journal', 'broker'):
                    (root / name).mkdir(mode=0o700)
                proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
                                         'holder', str(root), phase],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, start_new_session=True, env={**os.environ,
                                        'PYTHONDONTWRITEBYTECODE':'1'}, bufsize=1)
                try:
                    # select prevents indefinite readline when a fixture fails to start.
                    import select
                    ready, _, _ = select.select([proc.stdout], [], [], 8)
                    check(label + '-started', bool(ready) and proc.stdout.readline().strip() == 'READY')
                    os.kill(proc.pid, signum)
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.kill(proc.pid, signal.SIGKILL)
                        proc.communicate(timeout=5)
                        raise AssertionError(label + '-timeout')
                    check(label + '-terminated', proc.returncode is not None)
                    state = guard._read(root / 'journal')
                    check(label + '-phase-preserved', state is not None and state['phase'] == phase)
                    check(label + '-conservative-decision', guard.recovery_decision(root / 'journal') == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
                    with owner.owner_only(root / 'runtime'):
                        check(label + '-owner-lock-released', True)
                    with serialized.serialized(root / 'journal'):
                        check(label + '-journal-lock-released', True)
                    check(label + '-no-physical-artifacts', not list(root.rglob('*.sh')))
                finally:
                    if proc.poll() is None:
                        os.kill(proc.pid, signal.SIGKILL)
                        proc.communicate(timeout=5)
    return {'version':'0.6.0', 'mode':'offline-supervisor-real-process-interruption',
            'selftest':'passed', 'totalScenarios':len(rows), 'results':rows,
            'executionAuthorized':False, 'physicalExecutionEnabled':False,
            'commandsExecuted':0, 'logicalRecoveryVerified':False,
            'limitations':['Process signals are not power loss',
                           'Kernel lock release is not transaction recovery',
                           'No continuous worker authorization after coordinator death',
                           'No physical rollback or runtime entrypoint integration']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'holder'))
    parser.add_argument('root', nargs='?')
    parser.add_argument('phase', nargs='?')
    args = parser.parse_args()
    if args.command == 'holder':
        if not args.root or args.phase not in PHASES:
            parser.error('invalid holder arguments')
        holder(Path(args.root), args.phase)
    else:
        print(json.dumps(selftest(), indent=2))
