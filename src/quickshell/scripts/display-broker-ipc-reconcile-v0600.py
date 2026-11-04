#!/usr/bin/env python3
"""Offline-only stale IPC reconciliation. Never authorizes execution or logical recovery.

The lock is acquired before socket inspection. Same-UID hostile pathname races
are NOT excluded by this protocol; it is not a security boundary.
"""
import contextlib
import errno
import importlib.util
import os
from pathlib import Path
import socket
import stat

HERE = Path(__file__).resolve().parent

def load(filename, key):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

owner = load('display-admission-owner-v0600.py', 'ipc_owner')
serialized = load('display-transaction-serialized-v0600.py', 'ipc_serialized')
orphan = load('display-transaction-orphan-audit-v0600.py', 'ipc_orphan')

class IPCReconcileError(RuntimeError):
    pass

def _socket_status(path):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return 'ABSENT', None
    if not stat.S_ISSOCK(before.st_mode) or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600:
        return 'UNSAFE', before
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.3)
        try:
            sock.connect(str(path))
        except ConnectionRefusedError:
            return 'STALE_CANDIDATE', before
        except (TimeoutError, OSError):
            return 'AMBIGUOUS', before
        return 'LIVE', before
    finally:
        sock.close()

def reconcile(runtime_root, journal_root, broker_root):
    roots = tuple(Path(x) for x in (runtime_root, journal_root, broker_root))
    result = {'version':'0.6.0','mode':'offline-ipc-reconciliation',
              'executionAuthorized':False,'physicalExecutionEnabled':False,
              'cleanupAuthorized':False,'logicalRecoveryVerified':False,
              'commandsExecuted':0,'socketRemoved':False,'decision':'BLOCK_AND_INVESTIGATE'}
    try:
        if len({str(p.resolve(strict=True)) for p in roots}) != 3:
            raise IPCReconcileError('roots must be distinct')
        broker = roots[2]
        bs = broker.lstat()
        if not stat.S_ISDIR(bs.st_mode) or bs.st_uid != os.getuid() or stat.S_IMODE(bs.st_mode) != 0o700:
            raise IPCReconcileError('unsafe broker root')
        with owner.owner_only(roots[0]):
            with serialized.serialized(roots[1]):
                info = orphan.inventory(roots[1])
                result['journalDecision'] = info['decision']
                result['orphanCount'] = info['orphanCount']
                path = broker/'worker.sock'
                status, before = _socket_status(path)
                result['socketStatus'] = status
                if status == 'ABSENT':
                    result['decision'] = 'IPC_ABSENT_LOGICAL_STATE_UNVERIFIED'
                elif status == 'STALE_CANDIDATE':
                    # Only IPC cleanup, not transaction recovery. Do not follow links.
                    after = path.lstat()
                    if (before.st_dev, before.st_ino, before.st_mode, before.st_uid) != (after.st_dev, after.st_ino, after.st_mode, after.st_uid):
                        raise IPCReconcileError('socket identity changed')
                    path.unlink()
                    result['socketRemoved'] = True
                    result['decision'] = 'IPC_RECLAIMED_LOGICAL_STATE_UNVERIFIED'
                else:
                    result['decision'] = 'BLOCK_AND_INVESTIGATE'
    except (OSError, RuntimeError, ValueError) as exc:
        result['errorType'] = type(exc).__name__
        result['decision'] = 'BLOCK_AND_INVESTIGATE'
    return result
