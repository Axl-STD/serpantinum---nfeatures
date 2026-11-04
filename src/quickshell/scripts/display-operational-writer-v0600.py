#!/usr/bin/env python3
"""Offline operational writer facade. No display/systemd execution capability.

This is a cooperative, supervised journal entrypoint. Fixture preparation remains
separate and does not grant physical execution. No automatic recovery is attempted.
"""
import importlib.util
import os
from pathlib import Path
import threading

HERE = Path(__file__).resolve().parent

def load(name, key):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

boundary = load('display-runtime-writer-boundary-v0600.py', 'operational_boundary')

class OperationalDenied(RuntimeError):
    pass

class OperationalWriter:
    """One coordinator thread owns an offline supervised journal session.

    The only mutating method is transition(); no raw guard or physical executor
    is exposed by this facade. The underlying Python modules remain importable.
    """
    def __init__(self, runtime_root, journal_root, broker_root):
        self._roots = (Path(runtime_root), Path(journal_root), Path(broker_root))
        self._session = None
        self._thread = None
        self._closed = False

    def __enter__(self):
        if self._closed or self._session is not None:
            raise OperationalDenied('writer already used')
        session = boundary.open_supervised_session(*self._roots)
        session.__enter__()
        self._session = session
        self._thread = threading.get_ident()
        return self

    def _check(self):
        if self._session is None or self._closed or self._thread != threading.get_ident():
            raise OperationalDenied('inactive or foreign writer')
        self._session._live()
        if self._session.poisoned:
            raise OperationalDenied('ambiguous journal state')

    def transition(self, pid, generation, phase, operation='offline-probe'):
        self._check()
        return self._session.transition(pid, generation, phase, operation)

    def revoke(self, pid):
        self._check()
        return self._session.revoke(pid)

    def decision(self):
        self._check()
        return self._session.decision()

    def report(self):
        if self._session is None or self._closed or self._thread != threading.get_ident():
            raise OperationalDenied('inactive or foreign writer')
        result = self._session.report()
        result.update(operationalWriter=True, executionAuthorized=False,
                      physicalExecutionEnabled=False, commandsExecuted=0)
        return result

    def __exit__(self, *exc):
        if self._thread != threading.get_ident():
            raise OperationalDenied('foreign writer close')
        session = self._session
        if session is None:
            raise OperationalDenied('writer not active')
        self._closed = True
        try:
            return session.__exit__(*exc)
        finally:
            self._session = None
            self._thread = None


class FencedOperationalWriter(OperationalWriter):
    """Explicit schema-2 offline facade; schema-1 default remains unchanged.

    The session owns both locks. Never call fenced-serialized inside it.
    This is cooperative journal authorization, not a physical executor.
    """
    def __enter__(self):
        if self._closed or self._session is not None:
            raise OperationalDenied('writer already used')
        module = load('display-continuous-fenced-authority-v0600.py',
                      'operational_schema2_session')
        session = module.FencedContinuousSession(*self._roots)
        session.__enter__()
        self._session = session
        self._thread = threading.get_ident()
        return self

    def begin(self, operation, snapshot_digest):
        self._check()
        return self._session.begin(operation, snapshot_digest)

    def report(self):
        result = super().report()
        result.update(operationalSchema=2, schema2Integrated=True,
                      logicalRecoveryVerified=False,
                      authenticatedDelegationImplemented=False)
        return result
