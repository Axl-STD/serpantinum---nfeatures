#!/usr/bin/env python3
"""Offline-only fail-closed admission + serialized journal + orphan inspection.
Never authorizes display changes, deletion, confirmation, or rollback.
"""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

HERE = Path(__file__).resolve().parent

def load(filename, key):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

serialized = load('display-transaction-serialized-v0600.py', 'v0600_serialized')
orphan = load('display-transaction-orphan-audit-v0600.py', 'v0600_orphan')

@contextlib.contextmanager
def admitted(root, admission_root):
    # Independent, private, non-production lock namespace. This is not universal exclusion.
    root = serialized.guard._check_dir(root)
    admission_root = serialized.guard._check_dir(admission_root)
    if root.resolve() == admission_root.resolve():
        raise RuntimeError('journal and admission roots must differ')
    import fcntl
    lock = admission_root / '.offline-coordinator.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        s = os.fstat(fd)
        ps = lock.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or (s.st_dev,s.st_ino)!=(ps.st_dev,ps.st_ino):
            raise RuntimeError('unsafe coordinator lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with serialized.serialized(root):
            yield
    finally:
        os.close(fd)

def inspect(root, admission_root):
    try:
        with admitted(root, admission_root):
            info = orphan.inventory(root)
            # Never infer safe execution from an empty inventory or terminal journal.
            return {'decision': info['decision'], 'orphanCount': info['orphanCount'],
                    'orphans': info['orphans'], 'cleanupAuthorized': False,
                    'executionAuthorized': False, 'physicalExecutionEnabled': False,
                    'authenticatedDelegationImplemented': False,
                    'universalRuntimeExclusionVerified': False, 'commandsExecuted': 0}
    except (OSError, RuntimeError, serialized.guard.GuardError) as exc:
        return {'decision': 'BLOCK_AND_INVESTIGATE', 'errorType': type(exc).__name__,
                'cleanupAuthorized': False, 'executionAuthorized': False,
                'physicalExecutionEnabled': False, 'authenticatedDelegationImplemented': False,
                'universalRuntimeExclusionVerified': False, 'commandsExecuted': 0}

def selftest():
    results=[]
    def check(name, condition):
        results.append({'scenario': name, 'passed': bool(condition)})
        if not condition: raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-integrated-') as temp:
        base=Path(temp)
        root=base/'journal'; adm=base/'admission'
        root.mkdir(mode=0o700); adm.mkdir(mode=0o700)
        a=inspect(root,adm)
        check('empty-not-proof',a['decision']=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        check('empty-no-cleanup',a['cleanupAuthorized'] is False)
        with admitted(root,adm):
            p=serialized.guard.prepare(root,'fixture','digest')
            check('prepare-phase',p['phase']=='prepared')
            check('prepare-generation',p['generation']==1)
            check('journal-lock-contended',inspect(root,adm)['decision']=='BLOCK_AND_INVESTIGATE')
        check('prepared-blocks',inspect(root,adm)['decision']=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        with admitted(root,adm):
            p=serialized.guard.advance(root,1,'applying')
            check('apply-generation',p['generation']==2)
        check('applying-blocks',inspect(root,adm)['decision']=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        with admitted(root,adm):
            p=serialized.guard.advance(root,2,'awaiting_confirmation')
            check('awaiting-generation',p['generation']==3)
        check('awaiting-blocks',inspect(root,adm)['decision']=='BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        (root/'.intent-orphan').write_bytes(b'partial')
        os.chmod(root/'.intent-orphan',0o600)
        x=inspect(root,adm)
        check('orphan-detected',x['orphanCount']==1)
        check('orphan-preserved',(root/'.intent-orphan').exists())
        check('orphan-no-cleanup',x['cleanupAuthorized'] is False)
        (root/'.intent-orphan').chmod(0o644)
        check('unsafe-orphan-classified',inspect(root,adm)['orphans'][0]['classification']=='QUARANTINE_REVIEW')
        (root/'.intent-orphan').chmod(0o600)
        with admitted(root,adm):
            p=serialized.guard.advance(root,3,'rollback_required')
            check('rollback-required-generation',p['generation']==4)
            p=serialized.guard.advance(root,4,'rolled_back')
            check('rolled-back-generation',p['generation']==5)
        check('terminal-not-proof',inspect(root,adm)['decision']=='TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION')
        (root/'intent.json').write_bytes(b'{bad')
        check('corrupt-blocks',inspect(root,adm)['decision']=='BLOCK_AND_INVESTIGATE')
        (root/'intent.json').unlink()
        check('missing-not-proof',inspect(root,adm)['decision']=='NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        (root/'intent.json').symlink_to(base/'missing')
        check('symlink-blocks',inspect(root,adm)['decision']=='BLOCK_AND_INVESTIGATE')
        (root/'intent.json').unlink()
        (adm/'.offline-coordinator.lock').chmod(0o644)
        check('permissive-coordinator-blocks',inspect(root,adm)['decision']=='BLOCK_AND_INVESTIGATE')
        (adm/'.offline-coordinator.lock').chmod(0o600)
        adm.chmod(0o755)
        check('permissive-admission-root-blocks',inspect(root,adm)['decision']=='BLOCK_AND_INVESTIGATE')
        adm.chmod(0o700)
        root.chmod(0o755)
        check('permissive-journal-root-blocks',inspect(root,adm)['decision']=='BLOCK_AND_INVESTIGATE')
        root.chmod(0o700)
        check('same-root-rejected',inspect(root,root)['decision']=='BLOCK_AND_INVESTIGATE')
        for index in range(10):
            x=inspect(root,adm)
            check('repeat-fail-closed-%02d'%index, x['executionAuthorized'] is False and x['physicalExecutionEnabled'] is False and x['cleanupAuthorized'] is False)
        for field in ('executionAuthorized','physicalExecutionEnabled','cleanupAuthorized','authenticatedDelegationImplemented','universalRuntimeExclusionVerified'):
            check('no-overclaim-'+field,inspect(root,adm)[field] is False)
        check('no-display-commands',inspect(root,adm)['commandsExecuted']==0)
    return {'version':'0.6.0','mode':'offline-integration-fail-closed','selftest':'passed',
            'totalScenarios':len(results),'results':results,'commandsExecuted':0,
            'physicalExecutionEnabled':False,'executionAuthorized':False,
            'cleanupAuthorized':False,'authenticatedDelegationImplemented':False,
            'universalRuntimeExclusionVerified':False,
            'limitations':['Independent offline lock namespace, not legacy runtime admission',
                           'No physical execution, authenticated delegation, or automatic cleanup',
                           'External journal verification and rollback remain required']}

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['selftest'])
    parser.parse_args()
    print(json.dumps(selftest(),indent=2))
