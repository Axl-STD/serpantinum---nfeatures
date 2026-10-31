#!/usr/bin/env python3
"""Shared runtime admission: inherited open-file-description delegation, never env-only."""
import contextlib
import fcntl
import os
from pathlib import Path
import stat

ENV = 'SERPANTINUM_RUNTIME_ADMISSION_FD'
NAME = 'serpantinum-display-gate-' + str(os.getuid()) + '.lock'

class AdmissionError(RuntimeError):
    pass

def _root(runtime_dir=None):
    raw = runtime_dir if runtime_dir is not None else os.environ.get('XDG_RUNTIME_DIR', '')
    if not raw:
        raise AdmissionError('XDG_RUNTIME_DIR required')
    root = Path(raw)
    if not root.is_absolute() or root.is_symlink() or root == Path('/tmp'):
        raise AdmissionError('unsafe runtime root')
    st = root.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise AdmissionError('unsafe runtime root permissions')
    return root

def _validate(fd, root):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
        raise AdmissionError('unsafe admission descriptor')
    target = root / NAME
    if target.is_symlink():
        raise AdmissionError('unsafe admission path')
    path_st = target.stat()
    if (st.st_dev, st.st_ino) != (path_st.st_dev, path_st.st_ino):
        raise AdmissionError('descriptor does not match admission path')

@contextlib.contextmanager
def admission(runtime_dir=None):
    root = _root(runtime_dir)
    prior = os.environ.get(ENV)
    inherited = prior is not None
    if inherited:
        try:
            fd = int(prior)
            if fd < 3:
                raise ValueError('low fd')
            _validate(fd, root)
            # LOCK_NB succeeds for an inherited locked open-file-description.
            # If it was not locked, this operation acquires the lock rather than bypassing it.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ValueError, OSError, BlockingIOError) as exc:
            raise AdmissionError('invalid or contended delegated descriptor') from exc
        yield fd
        return
    fd = os.open(root / NAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _validate(fd, root)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdmissionError('runtime admission busy') from exc
        os.environ[ENV] = str(fd)
        try:
            yield fd
        finally:
            os.environ.pop(ENV, None)
    finally:
        os.close(fd)

def child_fds():
    value = os.environ.get(ENV)
    if value is None:
        return ()
    try:
        fd = int(value)
        if fd < 3:
            raise ValueError('low fd')
        _validate(fd, _root())
    except (ValueError, OSError) as exc:
        raise AdmissionError('invalid child admission fd') from exc
    return (fd,)
