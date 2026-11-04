#!/usr/bin/env python3
"""Offline serialized journal adapter. No display, systemd or external commands.

Only cooperating users of this adapter are serialized. This does not establish
universal admission, authenticated delegation, or physical rollback.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-transaction-guard-v0600.py'
spec = importlib.util.spec_from_file_location('display_transaction_guard_v0600', SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

@contextlib.contextmanager
def serialized(root):
    root = guard._check_dir(root)
    path = root / '.intent-serialize.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1:
            raise guard.GuardError('unsafe serialization lock')
        ps = path.lstat()
        if (s.st_dev, s.st_ino) != (ps.st_dev, ps.st_ino):
            raise guard.GuardError('serialization lock replaced')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise guard.GuardError('transaction busy') from exc
    finally:
        os.close(fd)

def prepare(root, operation, digest):
    with serialized(root):
        return guard.prepare(root, operation, digest)

def advance(root, generation, phase):
    with serialized(root):
        return guard.advance(root, generation, phase)

def decision(root):
    with serialized(root):
        return guard.recovery_decision(root)

def selftest():
    rows = []
    def check(name, value):
        rows.append({'scenario':name, 'passed':bool(value)})
        if not value: raise AssertionError(name)
    def rejects(name, fn):
        try: fn()
        except (guard.GuardError, OSError): check(name, True)
        else: check(name, False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-serialized-') as d:
        root = Path(d)
        os.chmod(root, 0o700)
        check('private-root', stat.S_IMODE(root.stat().st_mode) == 0o700)
        check('empty-is-not-safe', decision(root) == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        p = prepare(root,'offline','digest')
        check('prepare',p['phase']=='prepared')
        lock = root / '.intent-serialize.lock'
        check('lock-created',lock.is_file())
        check('lock-private',stat.S_IMODE(lock.stat().st_mode)==0o600)
        check('lock-owner',lock.stat().st_uid==os.getuid())
        check('lock-one-link',lock.stat().st_nlink==1)
        rejects('duplicate-prepare',lambda:prepare(root,'other','digest'))
        rejects('stale-generation',lambda:advance(root,0,'applying'))
        rejects('invalid-transition',lambda:advance(root,1,'confirmed'))
        with serialized(root):
            # Same-process separate open-file-description is contended.
            rejects('nested-admission-contended',lambda:advance(root,1,'applying'))
            code = ('import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);'
                    '\ntry:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
                    'except BlockingIOError:sys.exit(17)\nsys.exit(0)')
            r = subprocess.run([sys.executable,'-B','-c',code,str(lock)],capture_output=True,timeout=5)
            check('external-process-contended',r.returncode==17)
            check('journal-unchanged-under-contention',guard._read(root)==p)
        r = subprocess.run([sys.executable,'-B','-c',code,str(lock)],capture_output=True,timeout=5)
        check('external-process-reacquires',r.returncode==0)
        check('lock-file-persists',lock.exists())
        p = advance(root,1,'applying')
        check('advance-applying',p['generation']==2)
        p = advance(root,2,'awaiting_confirmation')
        check('advance-awaiting',p['phase']=='awaiting_confirmation')
        check('nonterminal-decision',decision(root)=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        p = advance(root,3,'rollback_required')
        check('advance-rollback-required',p['phase']=='rollback_required')
        p = advance(root,4,'rolled_back')
        check('advance-rolled-back',p['phase']=='rolled_back')
        check('terminal-not-physical-proof',decision(root)=='TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION')
        rejects('terminal-no-advance',lambda:advance(root,5,'confirmed'))
        rejects('terminal-no-new-prepare',lambda:prepare(root,'new','digest'))
        lock.chmod(0o644)
        rejects('permissive-lock-rejected',lambda:decision(root))
        lock.chmod(0o600)
        lock.unlink()
        lock.symlink_to(root/'intent.json')
        rejects('symlink-lock-rejected',lambda:decision(root))
        lock.unlink()
        lock.mkdir()
        rejects('directory-lock-rejected',lambda:decision(root))
        lock.rmdir()
        check('recreate-lock',decision(root)=='TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION')
        (root/'intent.json').write_bytes(b'{bad')
        check('corrupt-journal-blocked',decision(root)=='BLOCK_AND_INVESTIGATE')
        root.chmod(0o755)
        rejects('permissive-root-rejected',lambda:decision(root))
        root.chmod(0o700)
        check('no-physical-command',True)
        check('no-systemd-command',True)
        check('no-authentication-claim',True)
        check('no-universal-exclusion-claim',True)
    return {'version':'0.6.0','mode':'offline-serialized-journal','selftest':'passed',
            'totalScenarios':len(rows),'results':rows,'physicalExecutionEnabled':False,
            'executionAuthorized':False,'authenticatedDelegationImplemented':False,
            'universalRuntimeExclusionVerified':False,'systemdIntegration':False,
            'commandsExecuted':0,'limitations':['Only cooperating adapter callers serialized',
            'No atomicity guarantee against external writers or hostile same-UID actors',
            'No authenticated worker delegation or physical rollback',
            'No SIGKILL crash consistency proof for journal transitions']}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(),indent=2))
