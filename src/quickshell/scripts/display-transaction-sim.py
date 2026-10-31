#!/usr/bin/env python3
"""Display transaction state-machine simulator (NO monitor writes).

All actions operate on simulated state files. No hyprctl keyword, config writes,
or actual rollback is performed. The watchdog is a separate Python process,
not a production recovery guarantee.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

VERSION = 1


def state_dir():
    root = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
    path = root / ('serpantinum-display-sim-' + str(os.getuid()))
    if path.is_symlink():
        raise ValueError('State directory must not be a symlink')
    path.mkdir(mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid() or (path.stat().st_mode & 0o077):
        raise ValueError('Unsafe state directory permissions')
    return path


@contextlib.contextmanager
def locked(directory):
    path = directory / 'transaction.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def read(directory):
    path = directory / 'transaction.json'
    if not path.exists():
        return {'schemaVersion': VERSION, 'phase': 'idle', 'pending': False}
    if path.is_symlink():
        raise ValueError('Unsafe transaction state path')
    return json.loads(path.read_text(encoding='utf-8'))


def write(directory, state):
    fd, name = tempfile.mkstemp(prefix='.state-', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / 'transaction.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def expire_if_needed(directory, now):
    state = read(directory)
    if state.get('pending') and now >= state['deadline']:
        state.update(phase='simulated-rollback-timeout', pending=False, finishedAt=now)
        write(directory, state)
    return state


def start(directory, seconds, watchdog=True):
    with locked(directory):
        previous = expire_if_needed(directory, time.time())
        if previous.get('pending'):
            raise ValueError('A simulated transaction is already pending')
        now = time.time()
        state = {'schemaVersion': VERSION, 'phase': 'simulated-pending',
                 'pending': True, 'id': secrets.token_hex(12),
                 'startedAt': now, 'deadline': now + seconds,
                 'timeoutSeconds': seconds, 'applyEnabled': False,
                 'physicalOutputsChanged': False, 'persistentConfigChanged': False}
        write(directory, state)
    if watchdog:
        # Best-effort demonstration only; not the production recovery supervisor.
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                          '_watchdog', '--id', state['id']],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         close_fds=True)
    return state


def resolve(directory, ident, action, now=None):
    now = time.time() if now is None else now
    with locked(directory):
        state = expire_if_needed(directory, now)
        if not state.get('pending'):
            raise ValueError('No pending simulated transaction')
        if state.get('id') != ident:
            raise ValueError('Transaction ID mismatch')
        state.update(phase='simulated-confirmed' if action == 'confirm' else 'simulated-rollback-manual',
                     pending=False, finishedAt=now)
        write(directory, state)
        return state


def watchdog(directory, ident):
    while True:
        with locked(directory):
            state = read(directory)
            if state.get('id') != ident or not state.get('pending'):
                return
            remaining = state['deadline'] - time.time()
            if remaining <= 0:
                expire_if_needed(directory, time.time())
                return
        time.sleep(min(remaining, 1.0))


def selftest():
    with tempfile.TemporaryDirectory(prefix='serpantinum-display-test-') as temp:
        directory = Path(temp)
        first = start(directory, 15, watchdog=False)
        assert first['pending']
        try:
            start(directory, 15, watchdog=False)
        except ValueError:
            pass
        else:
            raise AssertionError('Concurrent start was accepted')
        assert resolve(directory, first['id'], 'confirm')['phase'] == 'simulated-confirmed'
        second = start(directory, 15, watchdog=False)
        assert resolve(directory, second['id'], 'rollback')['phase'] == 'simulated-rollback-manual'
        third = start(directory, 15, watchdog=False)
        with locked(directory):
            expired = expire_if_needed(directory, third['deadline'] + 0.01)
        assert expired['phase'] == 'simulated-rollback-timeout' and not expired['pending']
        fourth = start(directory, 15, watchdog=False)
        try:
            resolve(directory, 'wrong-id', 'confirm')
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid transaction ID was accepted')
        assert resolve(directory, fourth['id'], 'rollback')['phase'] == 'simulated-rollback-manual'
    return {'selftest': 'passed', 'cases': ['start', 'lock', 'confirm', 'manual rollback', 'timeout', 'wrong id'],
            'physicalOutputsChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'start', 'confirm', 'rollback', 'selftest', '_watchdog'])
    parser.add_argument('--id', help='Transaction ID for confirm/rollback')
    parser.add_argument('--seconds', type=int, default=15, help='Simulated timeout, 5-120 seconds')
    args = parser.parse_args()
    if args.action == 'selftest':
        print(json.dumps(selftest(), indent=2))
        return
    directory = state_dir()
    if args.action == '_watchdog':
        if not args.id:
            parser.error('--id is required')
        watchdog(directory, args.id)
        return
    if args.action == 'start':
        if not 5 <= args.seconds <= 120:
            parser.error('--seconds must be between 5 and 120')
        result = start(directory, args.seconds)
    elif args.action == 'status':
        with locked(directory):
            result = expire_if_needed(directory, time.time())
    else:
        if not args.id:
            parser.error('--id is required')
        result = resolve(directory, args.id, args.action)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
