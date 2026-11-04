#!/usr/bin/env python3
"""Offline-only transition supervisor: fail closed on ambiguous transition results.

This is a cooperative laboratory interface, not a display execution gateway.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = HERE / 'display-continuous-authority-v0600.py'
if source.is_symlink() or not source.is_file():
    raise RuntimeError('unsafe continuous authority dependency')
spec = importlib.util.spec_from_file_location('v0600_continuous_supervised', source)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class SupervisionDenied(RuntimeError):
    pass

class SupervisedSession(mod.ContinuousSession):
    """Stops further cooperative transitions after an ambiguous attempt.

    An exception after a journal write is not proof the write did not happen.
    """
    def __init__(self, *roots):
        super().__init__(*roots)
        self.poisoned = False
        self.last_committed = None

    def transition(self, pid, generation, phase, operation='offline-probe'):
        self._live()
        if self.poisoned:
            raise SupervisionDenied('session requires external reconciliation')
        # Preflight errors must not poison a session: no handshake has started.
        if type(generation) is not int or generation < 1 or type(pid) is not int or pid <= 0:
            raise SupervisionDenied('invalid request')
        guard = mod.base.serialized.guard
        before = guard._read(self.journal_root)
        if before is None or before['generation'] != generation or phase not in guard.EDGES[before['phase']]:
            raise SupervisionDenied('journal precondition rejected')
        if phase not in ('applying', 'awaiting_confirmation', 'rollback_required', 'uncertain'):
            raise SupervisionDenied('terminal or physical action forbidden')
        try:
            result = super().transition(pid, generation, phase, operation)
            self._live()
            after = guard._read(self.journal_root)
            if (after != result or after['generation'] != generation + 1
                    or after['phase'] != phase or
                    after['operation'] != before['operation'] or
                    after['snapshot_digest'] != before['snapshot_digest']):
                raise SupervisionDenied('post-write evidence mismatch')
        except BaseException:
            self.poisoned = True
            raise
        self.last_committed = (result['generation'], result['phase'])
        return result

    def report(self):
        result = super().report()
        result.update(supervisorPoisoned=self.poisoned,
                      lastCommitted=self.last_committed,
                      executionAuthorized=False, physicalExecutionEnabled=False,
                      logicalRecoveryVerified=False)
        return result
