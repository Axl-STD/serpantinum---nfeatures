#!/usr/bin/env python3
"""Offline recovery arbitration prototype. No display commands or automatic recovery."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time

STATES = {'PREPARED', 'ARMED', 'APPLIED', 'CONFIRMED', 'ROLLBACK_REQUIRED', 'ROLLED_BACK', 'UNCERTAIN'}
EDGES = {
    'PREPARED': {'ARMED', 'UNCERTAIN'},
    'ARMED': {'APPLIED', 'ROLLBACK_REQUIRED', 'UNCERTAIN'},
    'APPLIED': {'CONFIRMED', 'ROLLBACK_REQUIRED', 'UNCERTAIN'},
    'ROLLBACK_REQUIRED': {'ROLLED_BACK', 'UNCERTAIN'},
    'CONFIRMED': set(), 'ROLLED_BACK': set(), 'UNCERTAIN': set(),
}
class RecoveryDenied(RuntimeError):
    pass

def _root(path):
    root = Path(path)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise RecoveryDenied('unsafe root')
    st = root.stat()
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RecoveryDenied('unsafe root permissions')
    return root

def _read(root):
    path = root / 'recovery.json'
    if path.is_symlink():
        raise RecoveryDenied('symlink state')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 8192:
            raise RecoveryDenied('unsafe state')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            value = json.loads(handle.read(8193))
        if (not isinstance(value, dict) or set(value) != {'schema','id','state','deadline_ns','snapshot','generation'}
                or value['schema'] != 1 or value['state'] not in STATES
                or type(value['generation']) is not int or value['generation'] < 1
                or type(value['deadline_ns']) is not int or value['deadline_ns'] <= 0
                or not isinstance(value['id'], str) or len(value['id']) != 64
                or any(c not in '0123456789abcdef' for c in value['id'])
                or not isinstance(value['snapshot'], dict)
                or set(value['snapshot']) != {'name','width','height','refresh_mhz','scale_milli','transform'}):
            raise RecoveryDenied('invalid state')
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise RecoveryDenied('corrupt state') from exc
    finally:
        os.close(fd)

def _write(root, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    fd, temp = tempfile.mkstemp(prefix='.recovery-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, root / 'recovery.json')
        dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        os.close(fd)
        if os.path.exists(temp):
            os.unlink(temp)

@contextlib.contextmanager
def _locked(path):
    root = _root(path)
    lock = root / '.recovery.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
            raise RecoveryDenied('unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        now = lock.lstat()
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise RecoveryDenied('replaced lock')
        yield root
    finally:
        os.close(fd)

def prepare(root, snapshot, deadline_ns):
    if (type(deadline_ns) is not int or deadline_ns <= time.time_ns()
            or not isinstance(snapshot, dict) or set(snapshot) != {'name','width','height','refresh_mhz','scale_milli','transform'}
            or snapshot['name'] != 'eDP-1' or snapshot['width'] != 1920 or snapshot['height'] != 1080
            or snapshot['refresh_mhz'] not in (60060, 144060)
            or snapshot['scale_milli'] != 1000 or snapshot['transform'] != 0):
        raise RecoveryDenied('invalid snapshot or deadline')
    with _locked(root) as path:
        if _read(path) is not None:
            raise RecoveryDenied('existing recovery requires external reconciliation')
        value = dict(schema=1, id=secrets.token_hex(32), state='PREPARED', deadline_ns=deadline_ns, snapshot=snapshot, generation=1)
        _write(path, value)
        return value

def transition(root, token, expected_generation, destination, now_ns=None):
    with _locked(root) as path:
        value = _read(path)
        if value is None or not secrets.compare_digest(value['id'], token):
            raise RecoveryDenied('foreign or missing recovery')
        if type(expected_generation) is not int or expected_generation != value['generation']:
            raise RecoveryDenied('stale generation')
        if destination not in EDGES[value['state']]:
            raise RecoveryDenied('invalid transition')
        now = time.time_ns() if now_ns is None else now_ns
        if destination == 'CONFIRMED' and now >= value['deadline_ns']:
            raise RecoveryDenied('confirmation expired')
        if destination == 'ROLLED_BACK':
            raise RecoveryDenied('rollback requires independently verified restoration')
        if destination == 'APPLIED':
            raise RecoveryDenied('physical apply not implemented')
        value = dict(value, state=destination, generation=expected_generation + 1)
        _write(path, value)
        return value

def expire(root, now_ns=None):
    with _locked(root) as path:
        value = _read(path)
        if value is None:
            return 'NO_STATE'
        now = time.time_ns() if now_ns is None else now_ns
        if value['state'] in ('PREPARED', 'ARMED', 'APPLIED') and now >= value['deadline_ns']:
            value = dict(value, state='ROLLBACK_REQUIRED', generation=value['generation'] + 1)
            _write(path, value)
        return value['state']

def inspect(root):
    with _locked(root) as path:
        return _read(path)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['inspect','expire'])
    parser.add_argument('root')
    args = parser.parse_args()
    print(json.dumps(inspect(args.root) if args.command == 'inspect' else {'decision':expire(args.root)}, indent=2))
