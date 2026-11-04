#!/usr/bin/env python3
"""Offline-only bridge between cooperative evidence locks and operational writer.

No display/systemd commands. Never delegates a lock fd. No physical authorization.
"""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, key):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe bridge dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


operational = load('display-operational-writer-v0600.py', 'bridge_operational')
owner = load('display-admission-owner-v0600.py', 'bridge_owner')


class BridgeDenied(RuntimeError):
    pass


class RuntimeWriter:
    """An offline-only operational writer sharing the evidence lock pathname.

    Caller supplies a private test root. No inherited delegation is accepted.
    """
    def __init__(self, runtime_root, journal_root, broker_root):
        self.roots = tuple(Path(p) for p in (runtime_root, journal_root, broker_root))
        self._writer = None
        self._spent = False

    def __enter__(self):
        if self._writer is not None or self._spent:
            raise BridgeDenied('bridge already used')
        if owner.ENV in os.environ:
            raise BridgeDenied('inherited authority not permitted')
        writer = operational.OperationalWriter(*self.roots)
        writer.__enter__()
        self._writer = writer
        return self

    def _require(self):
        if self._writer is None:
            raise BridgeDenied('bridge inactive')
        return self._writer

    def transition(self, pid, generation, phase, operation='offline-probe'):
        return self._require().transition(pid, generation, phase, operation)

    def revoke(self, pid):
        return self._require().revoke(pid)

    def decision(self):
        return self._require().decision()

    def report(self):
        report = dict(self._require().report())
        report.update(bridgeScope='offline-cooperating-lock-path',
                      executionAuthorized=False, physicalExecutionEnabled=False,
                      authenticatedDelegationImplemented=False,
                      universalRuntimeExclusionVerified=False,
                      commandsExecuted=0)
        return report

    def __exit__(self, *args):
        writer = self._require()
        try:
            return writer.__exit__(*args)
        finally:
            self._spent = True
            self._writer = None
