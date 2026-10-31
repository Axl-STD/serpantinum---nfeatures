#!/usr/bin/env python3
"""v0.5.43: isolated coordination rehearsal, NOT a production coordinator.

Uses the v0.5.42 cooperative lock and a fail-closed reservation journal.
Never invokes compositor, systemd, or existing transaction controllers.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

VERSION = '0.5.43'
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
RECEIPT = 'coordination-rehearsal-v0543.json'


def lock_module():
    path = Path(__file__).with_name('display-global-lock.py')
    spec = importlib.util.spec_from_file_location('serpantinum_lock_v0542', path)
    if spec is None or spec.loader is None:
        raise ValueError('Missing lock dependency')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def safe_read(root):
    path = Path(root) / RECEIPT
    if path.is_symlink():
        raise ValueError('Symlinked reservation')
    if not path.exists():
        return None
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or s.st_size > 16384:
        raise ValueError('Unsafe reservation')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != VERSION or data.get('mode') != 'isolated-rehearsal-only' or data.get('phase') not in ('reserved', 'released') or not isinstance(data.get('transactionId'), str) or not TOKEN.fullmatch(data['transactionId']) or data.get('physicalExecutionEnabled') is not False or data.get('executionAuthorized') is not False or data.get('commandsExecuted') != 0:
        raise ValueError('Invalid reservation state')
    return data


def write_new(root, data):
    path = Path(root) / RECEIPT
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def result(token, phase):
    return {'version': VERSION, 'mode': 'isolated-rehearsal-only', 'phase': phase,
            'transactionId': token, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'executionAuthorized': False,
            'commandsExecuted': 0, 'supervisorsIntegrated': False}


def operate(root, token, action):
    if not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    lock = lock_module()
    with lock.exclusive(root):
        old = safe_read(root)
        if action == 'reserve':
            if old is not None:
                raise ValueError('Reservation already exists; no automatic reuse')
            value = result(token, 'reserved')
            write_new(root, value)
        elif action == 'release':
            if old is None or old['transactionId'] != token or old['phase'] != 'reserved':
                raise ValueError('No matching active reservation')
            # Fail closed: do not rewrite or delete reservation; release is a diagnostic result.
            value = result(token, 'release-observed')
        elif action == 'status':
            value = old if old is not None else {'version': VERSION, 'phase': 'empty', 'executionAuthorized': False}
        else:
            raise ValueError('Unknown operation')
        return value


def selftest():
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0543-') as root:
        os.chmod(root, 0o700)
        token = 'a' * 24
        assert operate(root, token, 'status')['phase'] == 'empty'
        assert operate(root, token, 'reserve')['phase'] == 'reserved'
        for candidate, action in [(token, 'reserve'), ('b'*24, 'release')]:
            try:
                operate(root, candidate, action)
            except ValueError:
                pass
            else:
                raise AssertionError('Unsafe operation accepted')
        assert operate(root, token, 'release')['phase'] == 'release-observed'
        assert operate(root, token, 'status')['phase'] == 'reserved'
        # A simulated initiator crash must leave the reservation blocking new work.
        try:
            operate(root, 'b'*24, 'reserve')
        except ValueError:
            pass
        else:
            raise AssertionError('Stale reservation automatically reused')
    return {'selftest': 'passed', 'scenarios': ['empty', 'reserve', 'duplicate-rejected',
            'wrong-identity-rejected', 'release-observed-only', 'stale-fail-closed'],
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'commandsExecuted': 0, 'supervisorsIntegrated': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'reserve', 'release', 'status'])
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    args = p.parse_args()
    try:
        if args.command == 'selftest':
            value = selftest()
        else:
            if args.root is None:
                raise ValueError('Explicit isolated root required')
            if args.command != 'status' and not args.token:
                raise ValueError('Token required')
            value = operate(args.root, args.token or 'a'*24, args.command)
        print(json.dumps(value, indent=2))
        return 0
    except (ValueError, OSError, AssertionError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
