#!/usr/bin/env python3
"""Offline owner-only admission candidate. No inherited delegation or display execution."""
import contextlib
import fcntl
import os
from pathlib import Path
import stat

class AdmissionDenied(RuntimeError):
    pass

NAME = 'serpantinum-display-gate-' + str(os.getuid()) + '.lock'
ENV = 'SERPANTINUM_RUNTIME_ADMISSION_FD'

def root_check(raw):
    root = Path(raw)
    if not root.is_absolute() or root == Path('/tmp'):
        raise AdmissionDenied('invalid root')
    try:
        s = root.lstat()
    except OSError as e:
        raise AdmissionDenied('missing root') from e
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise AdmissionDenied('unsafe root')
    return root

@contextlib.contextmanager
def owner_only(runtime_dir):
    """Reject inherited authority; obtain a new independent lock, fail closed."""
    if ENV in os.environ:
        raise AdmissionDenied('delegation disabled in owner-only candidate')
    root = root_check(runtime_dir)
    path = root / NAME
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as e:
        raise AdmissionDenied('lock open failed') from e
    try:
        s = os.fstat(fd)
        p = path.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
            or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1
            or (s.st_dev, s.st_ino) != (p.st_dev, p.st_ino)):
            raise AdmissionDenied('unsafe lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise AdmissionDenied('busy') from e
        # Recheck pathname after locking, to avoid claiming an unlinked lock.
        p = path.lstat()
        if (s.st_dev, s.st_ino) != (p.st_dev, p.st_ino):
            raise AdmissionDenied('lock replaced')
        yield fd
    finally:
        os.close(fd)
