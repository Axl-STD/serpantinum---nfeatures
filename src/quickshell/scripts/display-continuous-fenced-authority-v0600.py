#!/usr/bin/env python3
"""Offline schema-2 worker-authorized session; no physical execution.

The base session owns owner-only and serialization locks. Never reacquire them.
"""
import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent

def load(name,key):
    p=HERE/name
    if p.is_symlink() or not p.is_file(): raise RuntimeError('unsafe dependency')
    s=importlib.util.spec_from_file_location(key,p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

continuous=load('display-continuous-authority-v0600.py','fenced_continuous')
fence=load('display-session-fencing-journal-v0600.py','fenced_journal')

class FencedAuthorityDenied(RuntimeError): pass

class FencedContinuousSession(continuous.ContinuousSession):
    reclaim_stale_ipc = True
    """Single-use cooperative session; fail closed on ambiguous attempts."""
    def __init__(self,*roots):
        super().__init__(*roots)
        self.poisoned=False
        self.last_committed=None

    def begin(self,operation,digest):
        self._live()
        if self.poisoned: raise FencedAuthorityDenied('poisoned session')
        return fence.begin(self.journal_root,operation,digest)

    def transition(self,pid,generation,phase,operation='offline-probe'):
        self._live()
        if self.poisoned: raise FencedAuthorityDenied('reconciliation required')
        if type(pid) is not int or pid<=0 or pid in self.revoked:
            raise FencedAuthorityDenied('worker not eligible')
        if type(generation) is not int or generation<1 or phase not in (
            'applying','awaiting_confirmation','rollback_required','uncertain'):
            raise FencedAuthorityDenied('invalid transition')
        before=fence.read(self.journal_root)
        if before is None or before['generation']!=generation or phase not in fence.EDGES[before['phase']]:
            raise FencedAuthorityDenied('journal precondition')
        if before['coordinator_identity']!=fence._identity():
            raise FencedAuthorityDenied('foreign coordinator')
        try:
            worker_start=continuous.base.worker.starttime(pid)
        except continuous.base.worker.AuthorityError as exc:
            raise FencedAuthorityDenied('worker unavailable') from exc
        # Every transition requires a new handshake. An ACK is not physical authority.
        self.broker.register(pid,operation)
        try:
            result=self.broker.serve_once()
        except BaseException:
            self.poisoned=True
            raise
        finally:
            self.broker.allowed.pop(pid,None)
        try:
            self._live()
            if not result['accepted'] or result['peerPid']!=pid or result['operation']!=operation or pid in self.revoked:
                raise FencedAuthorityDenied('worker denied')
            if continuous.base.worker.starttime(pid)!=worker_start:
                raise FencedAuthorityDenied('worker incarnation changed')
            current=fence.read(self.journal_root)
            if current!=before:
                raise FencedAuthorityDenied('journal changed during handshake')
            self._live()
            written=fence.advance(self.journal_root,before['session_id'],generation,phase)
            self._live()
            if fence.read(self.journal_root)!=written:
                raise FencedAuthorityDenied('post-write mismatch')
        except BaseException:
            self.poisoned=True
            raise
        self.last_committed=(written['generation'],written['phase'])
        return written

    def decision(self):
        if not self.active: return 'BLOCK_AND_INVESTIGATE'
        return fence.decision(self.journal_root)

    def report(self):
        result=super().report()
        result.update(schema2Integrated=True,supervisorPoisoned=self.poisoned,
                      lastCommitted=self.last_committed,executionAuthorized=False,
                      physicalExecutionEnabled=False,commandsExecuted=0,
                      logicalRecoveryVerified=False)
        return result
