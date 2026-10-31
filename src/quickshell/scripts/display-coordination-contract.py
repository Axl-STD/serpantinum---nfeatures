#!/usr/bin/env python3
"""Serpantinum v0.5.41: read-only coordination contract.

Diagnostic inspection only. No authorization, locking, recovery or physical actions.
Snapshots are non-atomic; do not use this result as an execution precheck.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

TOKEN = re.compile(r'[0-9a-f]{24}\Z')
JOURNAL = 'recovery-protocol-v0531.json'


def secure_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or symlinked evidence')
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 1048576:
        raise ValueError('Unsafe evidence permissions or size')
    return json.loads(path.read_text(encoding='utf-8'))


def inspect(bundle_root=None, journal_root=None):
    result = {'version': '0.5.41', 'mode': 'read-only-contract',
              'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
              'commandsExecuted': 0, 'executionAuthorized': False,
              'atomicCrossStoreSnapshot': False}
    if bundle_root is not None:
        base = Path(bundle_root)
        if base.is_symlink() or not base.is_dir() or base.stat().st_uid != os.getuid():
            raise ValueError('Unsafe bundle root')
        path = base / 'serpantinum-display-v0513' / 'bundle.json'
        if not path.exists() and not path.is_symlink():
            result['bundle'] = {'phase': 'idle'}
        else:
            envelope = secure_json(path)
            payload = envelope['payload']
            canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            if hashlib.sha256(canonical).hexdigest() != envelope['sha256']:
                raise ValueError('Bundle integrity mismatch')
            if payload.get('safeToExecuteAutomatically') is not False or payload.get('physicalRollbackVerified') is not False or payload.get('commandsExecuted') != 0:
                raise ValueError('Unsafe bundle flags')
            result['bundle'] = {'phase': payload['phase'], 'transactionId': payload['id'], 'integrityVerified': True}
    if journal_root is not None:
        base = Path(journal_root)
        if base.is_symlink() or not base.is_dir() or base.stat().st_uid != os.getuid() or stat.S_IMODE(base.stat().st_mode) & 0o077:
            raise ValueError('Unsafe journal root')
        journal = secure_json(base / JOURNAL)
        state = journal['state']
        if journal.get('version') != '0.5.31' or journal.get('mode') != 'isolated-simulation-only':
            raise ValueError('Unknown journal format')
        if not isinstance(state, dict) or state.get('physicalExecutionEnabled') is not False or state.get('physicalRollbackVerified') is not False or state.get('commandsExecuted') != 0:
            raise ValueError('Unsafe journal flags')
        history = state.get('history')
        if not isinstance(history, list) or not history or len(history) > 32 or history[-1] != state.get('phase'):
            raise ValueError('Invalid journal history')
        if not isinstance(state.get('transactionId'), str) or not TOKEN.fullmatch(state['transactionId']):
            raise ValueError('Invalid transaction identity')
        result['journal'] = {'phase': state['phase'], 'transactionId': state['transactionId'], 'historyLength': len(history)}
    result['storesIndependent'] = True
    return result


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'serpantinum-display-v0513').mkdir()
        payload = {'phase': 'sealed-read-only', 'id': 'a'*24, 'safeToExecuteAutomatically': False, 'physicalRollbackVerified': False, 'commandsExecuted': 0}
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        file = root / 'serpantinum-display-v0513' / 'bundle.json'
        file.write_text(json.dumps({'payload': payload, 'sha256': hashlib.sha256(canonical).hexdigest()}))
        file.chmod(0o600)
        assert inspect(root)['bundle']['integrityVerified']
        payload['commandsExecuted'] = 1
        file.write_text(json.dumps({'payload': payload, 'sha256': hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}))
        try:
            inspect(root)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe bundle accepted')
        file.unlink()
        assert inspect(root)['bundle']['phase'] == 'idle'
    return {'selftest': 'passed', 'scenarios': 3, 'physicalExecutionEnabled': False, 'commandsExecuted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'selftest'])
    parser.add_argument('--bundle-root', type=Path)
    parser.add_argument('--journal-root', type=Path)
    args = parser.parse_args()
    try:
        data = selftest() if args.command == 'selftest' else inspect(args.bundle_root, args.journal_root)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
