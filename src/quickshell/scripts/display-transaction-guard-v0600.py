#!/usr/bin/env python3
"""v0.6.0 offline-only transaction guard; NEVER applies display settings.

Durable intent journal and conservative recovery decisions. This is NOT
worker authentication or physical rollback. No shell/systemd/Hyprland calls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

SCHEMA = 1
PHASES = {'prepared', 'applying', 'awaiting_confirmation', 'confirmed', 'rollback_required', 'rolled_back', 'uncertain'}
EDGES = {
    'prepared': {'applying', 'rollback_required'},
    'applying': {'awaiting_confirmation', 'rollback_required', 'uncertain'},
    'awaiting_confirmation': {'confirmed', 'rollback_required', 'uncertain'},
    'rollback_required': {'rolled_back', 'uncertain'},
    'confirmed': set(), 'rolled_back': set(), 'uncertain': set(),
}
class GuardError(RuntimeError):
    pass

def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def _check_dir(root):
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise GuardError('unsafe root')
    s = root.stat()
    if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise GuardError('unsafe root ownership or mode')
    return root

def _read(root):
    root = _check_dir(root)
    p = root / 'intent.json'
    if p.is_symlink():
        raise GuardError('symlink journal')
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 16384:
            raise GuardError('unsafe journal')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            data = f.read(16385)
        if len(data) > 16384:
            raise GuardError('oversized journal')
        wrapper = json.loads(data)
        payload = wrapper['payload']
        if not isinstance(payload, dict) or wrapper['sha256'] != hashlib.sha256(_canonical(payload)).hexdigest():
            raise GuardError('invalid journal checksum')
        if set(payload) != {'schema', 'generation', 'phase', 'operation', 'snapshot_digest'}:
            raise GuardError('invalid journal fields')
        if payload['schema'] != SCHEMA or type(payload['generation']) is not int or payload['generation'] < 1 or payload['phase'] not in PHASES:
            raise GuardError('invalid journal schema')
        if not all(isinstance(payload[k], str) and 0 < len(payload[k]) <= 128 for k in ('operation','snapshot_digest')):
            raise GuardError('invalid journal identity')
        return payload
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise GuardError('corrupt journal') from exc
    finally:
        os.close(fd)

def _write(root, payload):
    root = _check_dir(root)
    if (root / 'intent.json').is_symlink():
        raise GuardError('symlink journal')
    wrapped = {'payload': payload, 'sha256': hashlib.sha256(_canonical(payload)).hexdigest()}
    fd, name = tempfile.mkstemp(prefix='.intent-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        owned_fd = fd
        fd = -1  # fdopen owns and closes this descriptor, even on exceptions.
        with os.fdopen(owned_fd, 'wb') as f:
            f.write(_canonical(wrapped) + b'\n')
            f.flush()
            os.fsync(f.fileno())
        fd = -1
        os.replace(name, root / 'intent.json')
        dirfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(name):
            os.unlink(name)

def prepare(root, operation, snapshot_digest):
    if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in (operation, snapshot_digest)):
        raise GuardError('invalid operation or digest')
    previous = _read(root)
    if previous is not None:
        raise GuardError('journal already exists; manual reconciliation required')
    p = {'schema': SCHEMA, 'generation': 1, 'phase': 'prepared', 'operation': operation, 'snapshot_digest': snapshot_digest}
    _write(root, p)
    return p

def advance(root, expected_generation, phase):
    p = _read(root)
    if p is None:
        raise GuardError('missing journal')
    if type(expected_generation) is not int or p['generation'] != expected_generation:
        raise GuardError('stale generation')
    if phase not in EDGES[p['phase']]:
        raise GuardError('invalid transition')
    p = dict(p, phase=phase, generation=expected_generation + 1)
    _write(root, p)
    return p

def recovery_decision(root):
    try:
        p = _read(root)
    except (GuardError, OSError):
        return 'BLOCK_AND_INVESTIGATE'
    if p is None:
        return 'NO_JOURNAL_NOT_PROOF_OF_SAFETY'
    if p['phase'] in ('confirmed', 'rolled_back'):
        return 'TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION'
    return 'BLOCK_AND_RECONCILE_EXTERNAL_STATE'

def selftest():
    rows = []
    def check(name, ok):
        rows.append({'scenario': name, 'passed': bool(ok)})
        if not ok:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-') as d:
        root = Path(d); os.chmod(root, 0o700)
        check('empty-not-safe', recovery_decision(root) == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        check('root-private', stat.S_IMODE(root.stat().st_mode) == 0o700)
        p = prepare(root, 'offline-test', 'snapshot-placeholder')
        check('prepare', p['phase'] == 'prepared')
        check('journal-exists', (root/'intent.json').is_file())
        check('journal-private', stat.S_IMODE((root/'intent.json').stat().st_mode) == 0o600)
        check('journal-readable', _read(root) == p)
        check('journal-checksum', 'sha256' in json.loads((root/'intent.json').read_text()))
        check('prepare-blocks', recovery_decision(root) == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        def rejects(name, fn):
            try: fn()
            except (GuardError, OSError): check(name, True)
            else: check(name, False)
        rejects('duplicate-prepare', lambda: prepare(root, 'other', 'other'))
        rejects('stale-generation', lambda: advance(root, 0, 'applying'))
        rejects('invalid-skip', lambda: advance(root, 1, 'confirmed'))
        rejects('invalid-phase', lambda: advance(root, 1, 'banana'))
        p = advance(root, 1, 'applying'); check('apply-phase', p['phase'] == 'applying')
        check('apply-generation', p['generation'] == 2)
        check('apply-still-blocks', recovery_decision(root) == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        rejects('replayed-generation', lambda: advance(root, 1, 'awaiting_confirmation'))
        p = advance(root, 2, 'awaiting_confirmation'); check('awaiting', p['phase'] == 'awaiting_confirmation')
        check('awaiting-blocks', recovery_decision(root) == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        p = advance(root, 3, 'rollback_required'); check('rollback-required', p['phase'] == 'rollback_required')
        check('rollback-required-blocks', recovery_decision(root) == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        p = advance(root, 4, 'rolled_back'); check('rolled-back-record', p['phase'] == 'rolled_back')
        check('terminal-not-proof', recovery_decision(root) == 'TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION')
        rejects('terminal-immutable', lambda: advance(root, 5, 'confirmed'))
        rejects('prepare-after-terminal', lambda: prepare(root, 'new', 'digest'))
        original = (root/'intent.json').read_bytes()
        (root/'intent.json').write_bytes(original.replace(b'rolled_back', b'confirmedxx'))
        check('tamper-blocks', recovery_decision(root) == 'BLOCK_AND_INVESTIGATE')
        (root/'intent.json').write_bytes(original)
        os.chmod(root/'intent.json', 0o644)
        check('permissive-file-blocks', recovery_decision(root) == 'BLOCK_AND_INVESTIGATE')
        os.chmod(root/'intent.json', 0o600)
        os.chmod(root, 0o755)
        check('permissive-root-blocks', recovery_decision(root) == 'BLOCK_AND_INVESTIGATE')
        os.chmod(root, 0o700)
        (root/'intent.json').unlink()
        (root/'intent.json').symlink_to(root/'missing')
        check('symlink-blocks', recovery_decision(root) == 'BLOCK_AND_INVESTIGATE')
        (root/'intent.json').unlink()
        check('removed-not-safe', recovery_decision(root) == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        p = prepare(root, 'new', 'digest'); check('recreate-after-removal', p['generation'] == 1)
        p = advance(root, 1, 'rollback_required'); check('cancel-prepared', p['phase'] == 'rollback_required')
        p = advance(root, 2, 'uncertain'); check('uncertain-terminal', p['phase'] == 'uncertain')
        check('uncertain-blocks', recovery_decision(root) == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        rejects('uncertain-immutable', lambda: advance(root, 3, 'rolled_back'))
    return {'version':'0.6.0','mode':'offline-transaction-guard','selftest':'passed','totalScenarios':len(rows),'results':rows,'physicalExecutionEnabled':False,'executionAuthorized':False,'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,'commandsExecuted':0,'limitations':['Not connected to physical execution','No cross-process serialization; external admission required','Journal phase does not prove actual rollback','No authenticated worker delegation']}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))
if __name__ == '__main__':
    main()
