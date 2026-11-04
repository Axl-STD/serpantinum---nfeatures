#!/usr/bin/env python3
"""Offline failure-window characterization. No display or systemd operations.

Faults are injected only into disposable temporary directories. This is not
power-loss durability proof, authenticated delegation, or physical rollback.
"""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch

HERE=Path(__file__).resolve().parent

def load(filename, key):
    spec=importlib.util.spec_from_file_location(key, HERE / filename)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

guard=load('display-transaction-guard-v0600.py','v0600_guard')
serial=load('display-transaction-serialized-v0600.py','v0600_serial')

class InjectedFailure(OSError):
    pass

def selftest():
    rows=[]
    def record(name, condition, classification='PASS'):
        rows.append({'scenario':name,'passed':bool(condition),'classification':classification})
        if not condition:
            raise AssertionError(name)
    def fail_write(root, point, before=False):
        original=guard._read(root)
        if point=='replace':
            original_fn=guard.os.replace
            def fault(*args,**kwargs):
                if before: raise InjectedFailure('before replace')
                result=original_fn(*args,**kwargs)
                raise InjectedFailure('after replace')
            ctx=patch.object(guard.os,'replace',side_effect=fault)
        elif point=='fsync':
            original_fn=guard.os.fsync
            count=[0]
            def fault(fd):
                count[0]+=1
                if (before and count[0]==1) or (not before and count[0]==2):
                    raise InjectedFailure('fsync window')
                return original_fn(fd)
            ctx=patch.object(guard.os,'fsync',side_effect=fault)
        else:
            raise AssertionError(point)
        try:
            with ctx:
                guard.advance(root,original['generation'],'applying')
        except OSError as exc:
            record(point+('-early' if before else '-late')+'-exception',not (point=='fsync' and before and getattr(exc,'errno',None)==9))
        else:
            record(point+'-exception',False)
        observed=guard._read(root)
        expected='prepared' if before else 'applying'
        record(point+('-early' if before else '-late')+'-readable',observed['phase']==expected)
        record(point+('-early' if before else '-late')+'-decision',guard.recovery_decision(root)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        record(point+('-early' if before else '-late')+'-generation',observed['generation']==(1 if before else 2))
        record(point+('-early' if before else '-late')+'-private',stat.S_IMODE((root/'intent.json').stat().st_mode)==0o600)
        record(point+('-early' if before else '-late')+'-identity',observed['operation']==original['operation'])
        record(point+('-early' if before else '-late')+'-checksum',bool(guard._read(root)))
        record(point+('-early' if before else '-late')+'-temp-clean-or-gap',not list(root.glob('.intent-*')))
    for point,before in [('replace',True),('replace',False),('fsync',True),('fsync',False)]:
        with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-fault-') as temp:
            root=Path(temp)
            os.chmod(root,0o700)
            guard.prepare(root,'offline','digest')
            fail_write(root,point,before)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-leftovers-') as temp:
        root=Path(temp)
        os.chmod(root,0o700)
        guard.prepare(root,'offline','digest')
        original=guard._read(root)
        orphan=root/'.intent-orphan'
        orphan.write_bytes(b'{incomplete')
        os.chmod(orphan,0o600)
        record('orphan-not-promoted',guard._read(root)==original)
        record('orphan-does-not-approve',serial.decision(root)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        record('orphan-retained-for-investigation',orphan.exists())
        record('orphan-private',stat.S_IMODE(orphan.stat().st_mode)==0o600)
        record('orphan-no-auto-cleanup',orphan.exists())
        (root/'intent.json').write_bytes(b'{bad')
        record('corrupt-journal-fails-closed',serial.decision(root)=='BLOCK_AND_INVESTIGATE')
        record('orphan-cannot-repair-corruption',serial.decision(root)=='BLOCK_AND_INVESTIGATE')
        record('no-recovery-from-orphan',orphan.exists())
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-concurrency-') as temp:
        root=Path(temp)
        os.chmod(root,0o700)
        p=serial.prepare(root,'offline','digest')
        with serial.serialized(root):
            try: serial.advance(root,p['generation'],'applying')
            except (guard.GuardError, serial.guard.GuardError): record('nested-write-rejected',True)
            else: record('nested-write-rejected',False)
            record('nested-rejection-preserves-generation',guard._read(root)['generation']==1)
            record('nested-rejection-preserves-phase',guard._read(root)['phase']=='prepared')
        p=serial.advance(root,1,'applying')
        record('post-contention-advance',p['generation']==2)
        try: serial.advance(root,1,'awaiting_confirmation')
        except (guard.GuardError, serial.guard.GuardError): record('stale-writer-rejected',True)
        else: record('stale-writer-rejected',False)
        record('stale-writer-no-change',guard._read(root)==p)
        record('recovery-remains-conservative',serial.decision(root)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        record('lock-file-private',stat.S_IMODE((root/'.intent-serialize.lock').stat().st_mode)==0o600)
        record('lock-file-persists',(root/'.intent-serialize.lock').is_file())
    return {'version':'0.6.0','mode':'offline-failure-window-characterization','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'physicalExecutionEnabled':False,
            'executionAuthorized':False,'authenticatedDelegationImplemented':False,
            'universalRuntimeExclusionVerified':False,'systemdIntegration':False,
            'commandsExecuted':0,'limitations':['Simulated exceptions are not real power loss or SIGKILL',
            'A late fsync failure may leave a visible new generation without durability proof',
            'No physical rollback, worker authentication, or universal admission']}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(),indent=2))
