#!/usr/bin/env python3
"""Offline coordinator-only, fresh worker handshake per journal transition.

Not a physical executor or a security boundary against same-UID code.
"""
import importlib.util
import os
import threading
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(name, key):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

base = load('display-authority-session-v0600.py', 'continuous_base')

class ContinuityDenied(RuntimeError):
    pass

class ContinuousSession(base.Session):
    """Single coordinator thread owns the session and all journal writes."""
    def __init__(self, runtime_root, journal_root, broker_root):
        super().__init__(runtime_root, journal_root, broker_root)
        self.revoked = set()
        self.owner_pid = None
        self.owner_starttime = None
        self.owner_thread = None
        self.used = set()
        self._resource_identities = None

    def __enter__(self):
        super().__enter__()
        self.owner_pid = os.getpid()
        self.owner_thread = threading.get_ident()
        self.owner_starttime = base.worker.starttime(self.owner_pid)
        try:
            self._resource_identities = self._capture_resources()
        except BaseException:
            super().__exit__(None, None, None)
            self.owner_pid = self.owner_thread = self.owner_starttime = None
            raise
        return self

    def _capture_resources(self):
        paths = (self.runtime_root / base.owner.NAME,
                 self.journal_root / '.intent-serialize.lock',
                 self.broker.path)
        identities = []
        for index, path in enumerate(paths):
            st = path.lstat()
            kind = stat.S_ISSOCK if index == 2 else stat.S_ISREG
            if (not kind(st.st_mode) or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o600):
                raise ContinuityDenied('unsafe session resource')
            if index != 2 and st.st_nlink != 1:
                raise ContinuityDenied('linked session lock')
            identities.append((st.st_dev, st.st_ino, st.st_mode, st.st_uid))
        return tuple(identities)

    def _live(self):
        if self.owner_thread is None or threading.get_ident() != self.owner_thread:
            raise ContinuityDenied('foreign coordinator thread')
        if not self.active or self.broker is None or self.broker.closed:
            raise ContinuityDenied('inactive session')
        if os.getpid() != self.owner_pid or base.worker.starttime(os.getpid()) != self.owner_starttime:
            raise ContinuityDenied('coordinator identity changed')
        if self._stack is None or self._resource_identities is None:
            raise ContinuityDenied('locks unavailable')
        try:
            observed = self._capture_resources()
        except (OSError, ContinuityDenied) as exc:
            raise ContinuityDenied('session resource unavailable') from exc
        if observed != self._resource_identities:
            raise ContinuityDenied('session resource replaced')

    def revoke(self, pid):
        self._live()
        self.revoked.add(pid)
        self.broker.allowed.pop(pid, None)

    def transition(self, pid, generation, phase, operation='offline-probe'):
        """Register one fresh request; caller must arrange peer request before serving.

        This function blocks waiting for a worker socket request; no physical commands.
        """
        self._live()
        if type(pid) is not int or pid <= 0 or pid in self.revoked:
            raise ContinuityDenied('worker not eligible')
        if type(generation) is not int or generation < 1:
            raise ContinuityDenied('invalid generation')
        if phase not in ('applying', 'awaiting_confirmation', 'rollback_required', 'uncertain'):
            raise ContinuityDenied('physical or terminal phase forbidden')
        current = base.serialized.guard._read(self.journal_root)
        if current is None or current['generation'] != generation:
            raise ContinuityDenied('journal generation mismatch')
        if phase not in base.serialized.guard.EDGES[current['phase']]:
            raise ContinuityDenied('invalid journal edge')
        # Capture worker incarnation and journal identity before the handshake.
        # A new process reusing the PID must never inherit this request.
        try:
            worker_start = base.worker.starttime(pid)
        except base.worker.AuthorityError as exc:
            raise ContinuityDenied('worker identity unavailable') from exc
        expected_identity = (current['operation'], current['snapshot_digest'],
                             current['phase'], current['generation'])
        # Registration and response are fresh for each transition. No persistent ACK.
        self.broker.register(pid, operation)
        try:
            result = self.broker.serve_once()
        finally:
            self.broker.allowed.pop(pid, None)
        self._live()
        if not result['accepted'] or result['peerPid'] != pid or result['operation'] != operation or pid in self.revoked:
            raise ContinuityDenied('worker denied')
        try:
            if base.worker.starttime(pid) != worker_start:
                raise ContinuityDenied('worker incarnation changed')
        except base.worker.AuthorityError as exc:
            raise ContinuityDenied('worker exited before commit') from exc
        # No nested serialized lock: session already holds it.
        current = base.serialized.guard._read(self.journal_root)
        if current is None or (current['operation'], current['snapshot_digest'],
                               current['phase'], current['generation']) != expected_identity:
            raise ContinuityDenied('journal changed')
        self._live()
        # Offline journal mutation only. This cannot prevent an adversarial
        # same-UID process from bypassing the cooperative protocol.
        return base.serialized.guard.advance(self.journal_root, generation, phase)

    def __exit__(self, *args):
        # Reject foreign-thread close without releasing the owner's resources.
        if self.owner_thread is None or threading.get_ident() != self.owner_thread:
            raise ContinuityDenied('foreign coordinator thread')
        # Even if a resource was replaced, release all held descriptors.
        # A failed liveness check must not strand an active broker or lock.
        failure = None
        try:
            self._live()
        except ContinuityDenied as exc:
            failure = exc
        self.revoked.clear()
        self.used.clear()
        self.owner_pid = None
        self.owner_starttime = None
        self.owner_thread = None
        self._resource_identities = None
        try:
            result = super().__exit__(*args)
        finally:
            self.active = False
        if failure is not None and args[0] is None:
            raise failure
        return result

    def report(self):
        result = super().report()
        result.update(continuousOfflineChecks=True, authenticatedDelegationImplemented=False,
                      physicalExecutionEnabled=False, executionAuthorized=False)
        return result
