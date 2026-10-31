#!/usr/bin/env python3
"""Serpantinum v0.5.50: read-only coordination contract.

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



# This function only classifies caller-supplied diagnostic dictionaries. It does
# not authenticate evidence, take a cross-store lock, or authorize any action.
DIAGNOSTIC_PHASES = ('empty', 'reserved', 'diagnostic-only', 'unverifiable',
                     'terminal-observed')


def classify_diagnostic(reservation=None, evidence=None):
    """Normalize offline diagnostic states; unknown/unsafe input fails closed."""
    if reservation is not None and not isinstance(reservation, dict):
        raise ValueError('Invalid reservation envelope')
    if evidence is not None and not isinstance(evidence, dict):
        raise ValueError('Invalid evidence envelope')
    for source in (reservation, evidence):
        if source is None:
            continue
        for flag in ('executionAuthorized', 'physicalExecutionEnabled',
                     'physicalRollbackVerified', 'finalizationAuthorized',
                     'reservationReusable'):
            if flag in source and source[flag] is not False:
                raise ValueError('Unsafe diagnostic flag: ' + flag)
        if 'commandsExecuted' in source and (type(source['commandsExecuted']) is not int or source['commandsExecuted'] != 0):
            raise ValueError('Unsafe diagnostic command count')
    if reservation is None:
        if evidence is not None:
            raise ValueError('Unbound evidence without reservation')
        phase, token = 'empty', None
    else:
        token = reservation.get('transactionId')
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('Invalid reservation identity')
        if reservation.get('phase') != 'reserved':
            raise ValueError('Unknown reservation phase')
        if reservation.get('executionAuthorized') is not False or reservation.get('physicalExecutionEnabled') is not False or reservation.get('commandsExecuted') != 0:
            raise ValueError('Incomplete reservation safety fields')
        if evidence is None:
            phase = 'reserved'
        else:
            if evidence.get('transactionId') != token:
                raise ValueError('Cross-evidence identity mismatch')
            if evidence.get('executionAuthorized') is not False or evidence.get('physicalExecutionEnabled') is not False or evidence.get('commandsExecuted') != 0:
                raise ValueError('Incomplete evidence safety fields')
            assessment = evidence.get('assessment')
            if assessment == 'evidence-missing-or-unverifiable':
                phase = 'unverifiable'
            elif assessment in ('evidence-contradictory', 'retained-test-unit-observed-only'):
                phase = 'diagnostic-only'
            elif assessment == 'diagnostic-terminal-observed':
                # A terminal claim is still only an untrusted observation.
                phase = 'terminal-observed'
            else:
                raise ValueError('Unknown evidence assessment')
    return {'version': '0.5.50', 'mode': 'offline-diagnostic-classification',
            'phase': phase, 'transactionId': token,
            'atomicCrossStoreSnapshot': False, 'evidenceAuthenticated': False,
            'supervisorsIntegrated': False, 'finalizationAuthorized': False,
            'reservationReusable': False, 'executionAuthorized': False,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0}


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
    token = 'a' * 24
    reservation = {'transactionId': token, 'phase': 'reserved',
                   'executionAuthorized': False, 'physicalExecutionEnabled': False,
                   'commandsExecuted': 0}
    evidence = {'transactionId': token, 'assessment': 'evidence-missing-or-unverifiable',
                'executionAuthorized': False, 'physicalExecutionEnabled': False,
                'commandsExecuted': 0}
    scenarios = ['bundle-valid', 'bundle-unsafe-rejected', 'bundle-idle']
    assert classify_diagnostic()['phase'] == 'empty'
    scenarios.append('empty')
    assert classify_diagnostic(reservation)['phase'] == 'reserved'
    scenarios.append('reserved')
    assert classify_diagnostic(reservation, evidence)['phase'] == 'unverifiable'
    scenarios.append('unverifiable')
    for label, changed in [('cross-identity-rejected', dict(evidence, transactionId='b'*24)),
                           ('unsafe-flag-rejected', dict(evidence, executionAuthorized=True))]:
        try:
            classify_diagnostic(reservation, changed)
        except ValueError:
            scenarios.append(label)
        else:
            raise AssertionError(label)
    assert classify_diagnostic(reservation, dict(evidence, assessment='evidence-contradictory'))['phase'] == 'diagnostic-only'
    scenarios.append('diagnostic-only')
    assert classify_diagnostic(reservation, dict(evidence, assessment='diagnostic-terminal-observed'))['phase'] == 'terminal-observed'
    scenarios.append('terminal-observed-not-authorized')
    return {'version': '0.5.50', 'selftest': 'passed', 'scenarios': scenarios,
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'commandsExecuted': 0}


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
