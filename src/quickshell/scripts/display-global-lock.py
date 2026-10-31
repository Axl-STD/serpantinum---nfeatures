#!/usr/bin/env python3
"""Serpantinum v0.5.42: isolated cooperative lock experiment; no display actions."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

VERSION = '0.5.42'


def private_root(path):
    p = Path(path).absolute()
    if p.is_symlink() or not p.is_dir():
        raise ValueError('Root must be a real directory')
    s = p.stat()
    if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise ValueError('Root must be owned by user with mode 0700')
    return p


@contextlib.contextmanager
def exclusive(root):
    folder = private_root(root)
    path = folder / 'global.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1:
            raise ValueError('Unsafe lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Lock already held') from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def report(phase):
    return {'version': VERSION, 'mode': 'isolated-lock-test-only', 'phase': phase,
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def acquire(root, seconds):
    if not 0 <= seconds <= 30:
        raise ValueError('Hold duration must be 0..30')
    with exclusive(root):
        print(json.dumps(report('lock-held')), flush=True)
        time.sleep(seconds)
    print(json.dumps(report('lock-released')), flush=True)


def selftest():
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0542-') as root:
        os.chmod(root, 0o700)
        with exclusive(root):
            child = subprocess.run([sys.executable, __file__, 'acquire', '--root', root],
                                   capture_output=True, text=True, timeout=5)
            assert child.returncode == 2 and 'Lock already held' in child.stderr
        acquire(root, 0)
        child = subprocess.Popen([sys.executable, __file__, 'acquire', '--root', root, '--hold', '30'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = child.stdout.readline()
            assert json.loads(line)['phase'] == 'lock-held'
            child.kill()
            child.wait(timeout=5)
            with exclusive(root):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        bad = Path(root) / 'global.lock'
        bad.unlink()
        bad.symlink_to(Path(root) / 'target')
        try:
            with exclusive(root):
                pass
        except OSError:
            pass
        else:
            raise AssertionError('Symlink accepted')
        bad.unlink()
        os.chmod(root, 0o755)
        try:
            with exclusive(root):
                pass
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe permissions accepted')
    return {**report('selftest-passed'), 'scenarios': ['contention-rejected', 'released-after-exit',
            'released-after-kill', 'symlink-rejected', 'unsafe-root-rejected']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'acquire'])
    p.add_argument('--root', type=Path)
    p.add_argument('--hold', type=float, default=0)
    a = p.parse_args()
    try:
        if a.command == 'selftest':
            print(json.dumps(selftest(), indent=2))
        else:
            if a.root is None:
                raise ValueError('Explicit isolated root required')
            acquire(a.root, a.hold)
        return 0
    except (ValueError, OSError, AssertionError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False,
                          'physicalExecutionEnabled': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
