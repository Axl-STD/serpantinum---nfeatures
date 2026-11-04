#!/usr/bin/env python3
"""Offline coordinator session. ACK is observation, never execution authorization."""
import contextlib
import importlib.util
import os
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(filename, key):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

owner = load('display-admission-owner-v0600.py', 'owner_v0600')
worker = load('display-worker-authority-v0600.py', 'worker_v0600')
serialized = load('display-transaction-serialized-v0600.py', 'serialized_v0600')

class SessionError(RuntimeError):
    pass

class Session:
    def __init__(self, runtime_root, journal_root, broker_root):
        self.runtime_root = Path(runtime_root)
        self.journal_root = Path(journal_root)
        self.broker_root = Path(broker_root)
        self.active = False
        self.acknowledged = set()
        self._stack = None
        self.broker = None

    reclaim_stale_ipc = False

    def _reclaim_stale_ipc_under_locks(self):
        """Cooperative IPC cleanup only. Both coordinator locks must be held."""
        root = self.broker_root
        st = root.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise SessionError('unsafe broker directory')
        # Reuse the existing IPC status classifier; never invoke reconcile(),
        # which would try to reacquire our already-held locks.
        ipc = load('display-broker-ipc-reconcile-v0600.py', 'session_ipc_reconcile')
        path = root / 'worker.sock'
        status, before = ipc._socket_status(path)
        if status == 'ABSENT':
            return
        if status != 'STALE_CANDIDATE':
            raise SessionError('IPC occupied or ambiguous: ' + status)
        after = path.lstat()
        identity = lambda x: (x.st_dev, x.st_ino, x.st_mode, x.st_uid)
        if identity(before) != identity(after):
            raise SessionError('IPC identity changed')
        path.unlink()
        # This is NOT logical journal recovery; begin() must still reject
        # any existing schema-2 journal from an earlier incarnation.

    def __enter__(self):
        roots = (self.runtime_root, self.journal_root, self.broker_root)
        if len({str(p.resolve(strict=True)) for p in roots}) != 3:
            raise SessionError('roots must be distinct')
        stack = contextlib.ExitStack()
        try:
            # Acquire in fixed order. No inherited admission fd.
            stack.enter_context(owner.owner_only(self.runtime_root))
            stack.enter_context(serialized.serialized(self.journal_root))
            if self.reclaim_stale_ipc:
                self._reclaim_stale_ipc_under_locks()
            self.broker = stack.enter_context(worker.Broker(self.broker_root))
            self.active = True
            self._stack = stack
            return self
        except BaseException:
            stack.close()
            raise

    def register(self, pid, operation='offline-probe'):
        if not self.active:
            raise SessionError('inactive session')
        self.broker.register(pid, operation)

    def handshake(self):
        if not self.active:
            raise SessionError('inactive session')
        result = self.broker.serve_once()
        if result['accepted']:
            self.acknowledged.add(result['peerPid'])
        return result

    def decision(self):
        if not self.active:
            return 'BLOCK_AND_INVESTIGATE'
        return serialized.guard.recovery_decision(self.journal_root)

    def __exit__(self, *exc):
        self.active = False
        self.acknowledged.clear()
        try:
            return self._stack.__exit__(*exc)
        finally:
            self.broker = None
            self._stack = None

    def report(self):
        return {'sessionActive': self.active, 'acknowledgedWorkers': len(self.acknowledged),
                'executionAuthorized': False, 'physicalExecutionEnabled': False,
                'authenticatedDelegationImplemented': False,
                'universalRuntimeExclusionVerified': False, 'cleanupAuthorized': False,
                'commandsExecuted': 0}
