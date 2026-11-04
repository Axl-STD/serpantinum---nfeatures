#!/usr/bin/env python3
"""Offline schema-2 journal under owner-only and serialization locks.

Not a worker authorization mechanism. Existing journals are never adopted or removed.
"""
import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent

def load(name,key):
    p=HERE/name
    if p.is_symlink() or not p.is_file(): raise RuntimeError('unsafe dependency')
    s=importlib.util.spec_from_file_location(key,p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

fence=load('display-session-fencing-journal-v0600.py','fenced_schema2')
serialized=load('display-transaction-serialized-v0600.py','fenced_serialized')
owner=load('display-admission-owner-v0600.py','fenced_owner')

class FencedDenied(RuntimeError): pass

class FencedJournal:
    def __init__(self,runtime_root,journal_root):
        self.runtime_root=Path(runtime_root)
        self.journal_root=Path(journal_root)
        self._owner=None
        self._serial=None
        self._active=False
        self._spent=False

    def __enter__(self):
        if self._active or self._spent: raise FencedDenied('single-use session')
        import contextlib
        stack=contextlib.ExitStack()
        try:
            stack.enter_context(owner.owner_only(self.runtime_root))
            stack.enter_context(serialized.serialized(self.journal_root))
        except BaseException:
            stack.close();raise
        self._serial=stack
        self._active=True
        return self

    def _require(self):
        if not self._active or self._serial is None: raise FencedDenied('inactive session')

    def begin(self,operation,digest):
        self._require()
        return fence.begin(self.journal_root,operation,digest)

    def advance(self,session_id,generation,phase):
        self._require()
        return fence.advance(self.journal_root,session_id,generation,phase)

    def decision(self):
        self._require()
        return fence.decision(self.journal_root)

    def __exit__(self,*args):
        self._require()
        self._active=False
        self._spent=True
        try:return self._serial.__exit__(*args)
        finally:self._serial=None
