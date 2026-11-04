#!/usr/bin/env python3
"""Offline-only conservative orphan inventory. Never removes files or authorizes display operations."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-transaction-guard-v0600.py'
spec = importlib.util.spec_from_file_location('display_transaction_guard', SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def inventory(root):
    root = guard._check_dir(root)
    entries = []
    for name in sorted(os.listdir(root)):
        if not name.startswith('.intent-') or name == '.intent-serialize.lock':
            continue
        path = root / name
        s = path.lstat()
        classification = 'QUARANTINE_REVIEW'
        if stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1:
            classification = 'ORPHAN_REVIEW_REQUIRED'
        entries.append({'name': name, 'classification': classification})
    decision = guard.recovery_decision(root)
    return {'decision': decision, 'orphanCount': len(entries), 'orphans': entries,
            'cleanupAuthorized': False, 'physicalExecutionEnabled': False,
            'executionAuthorized': False, 'commandsExecuted': 0}


def selftest():
    rows = []
    def check(name, value):
        rows.append({'scenario': name, 'passed': bool(value)})
        if not value:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='serpantinum-orphan-audit-') as d:
        root = Path(d)
        os.chmod(root, 0o700)
        def scan(): return inventory(root)
        check('empty-count', scan()['orphanCount'] == 0)
        check('empty-not-safe', scan()['decision'] == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        check('empty-no-cleanup', scan()['cleanupAuthorized'] is False)
        guard.prepare(root, 'fixture', 'digest')
        check('prepared-blocks', scan()['decision'] == 'BLOCK_AND_RECONCILE_EXTERNAL_STATE')
        names = ['.intent-a', '.intent-b', '.intent-c', '.intent-d']
        for i, name in enumerate(names):
            p = root / name
            p.write_bytes(b'fixture' * (i + 1))
            p.chmod(0o600)
            check('regular-count-' + str(i), scan()['orphanCount'] == i + 1)
            check('regular-review-' + str(i), scan()['orphans'][i]['classification'] == 'ORPHAN_REVIEW_REQUIRED')
            check('regular-retained-' + str(i), p.exists())
        (root / '.intent-a').chmod(0o644)
        check('permissive-quarantine', scan()['orphans'][0]['classification'] == 'QUARANTINE_REVIEW')
        (root / '.intent-a').chmod(0o600)
        (root / '.intent-b').unlink()
        (root / '.intent-b').symlink_to(root / 'intent.json')
        check('symlink-quarantine', scan()['orphans'][1]['classification'] == 'QUARANTINE_REVIEW')
        check('symlink-retained', (root / '.intent-b').is_symlink())
        (root / '.intent-c').unlink()
        (root / '.intent-c').mkdir(mode=0o700)
        check('directory-quarantine', scan()['orphans'][2]['classification'] == 'QUARANTINE_REVIEW')
        check('directory-retained', (root / '.intent-c').is_dir())
        check('journal-unmodified', guard._read(root)['phase'] == 'prepared')
        check('no-cleanup-with-orphans', scan()['cleanupAuthorized'] is False)
        check('no-physical', scan()['physicalExecutionEnabled'] is False)
        check('no-authorization', scan()['executionAuthorized'] is False)
        check('no-commands', scan()['commandsExecuted'] == 0)
        (root / '.intent-serialize.lock').write_bytes(b'lock')
        check('serialization-lock-excluded', scan()['orphanCount'] == 4)
        check('sorted-names', [x['name'] for x in scan()['orphans']] == names)
        (root / 'intent.json').write_bytes(b'{broken')
        check('corruption-blocks', scan()['decision'] == 'BLOCK_AND_INVESTIGATE')
        check('corruption-does-not-clean', scan()['cleanupAuthorized'] is False)
        (root / 'intent.json').unlink()
        check('missing-journal-not-safe', scan()['decision'] == 'NO_JOURNAL_NOT_PROOF_OF_SAFETY')
        check('missing-journal-orphans-retained', scan()['orphanCount'] == 4)
        (root / '.intent-b').unlink()
        (root / '.intent-c').rmdir()
        (root / '.intent-a').unlink()
        (root / '.intent-d').unlink()
        check('explicit-fixture-removal', scan()['orphanCount'] == 0)
        root.chmod(0o755)
        try:
            scan()
        except guard.GuardError:
            check('permissive-root-rejected', True)
        else:
            check('permissive-root-rejected', False)
        root.chmod(0o700)
    return {'version': '0.6.0', 'mode': 'offline-orphan-inventory', 'selftest': 'passed',
            'totalScenarios': len(rows), 'results': rows, 'cleanupAuthorized': False,
            'physicalExecutionEnabled': False, 'executionAuthorized': False, 'commandsExecuted': 0,
            'limitations': ['Inventory only; no automatic deletion or rollback',
                            'Same-UID hostile races and durable power-loss recovery not proven',
                            'Not integrated into physical execution']}

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['selftest'])
    p.parse_args()
    print(json.dumps(selftest(), indent=2))
