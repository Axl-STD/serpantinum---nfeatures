#!/usr/bin/env python3
"""Serpantinum v0.5.55: read-only coordination contract.

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
    """Read JSON through a verified descriptor; no symlink following at leaf/parent.

    This does not make ancestor directories immutable or cross-store reads atomic.
    """
    path = Path(path)
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    parent_fd = os.open(path.parent, dir_flags)
    try:
        parent_info = os.fstat(parent_fd)
        if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid()
                or stat.S_IMODE(parent_info.st_mode) & 0o022):
            raise ValueError('Unsafe evidence parent directory')
        fd = os.open(path.name, file_flags, dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
                    or info.st_size > 1048576):
                raise ValueError('Unsafe evidence descriptor')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(1048577)
            if len(raw) > 1048576:
                raise ValueError('Evidence exceeds size limit')
            return json.loads(raw.decode('utf-8'))
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def selftest_secure_json():
    import tempfile
    scenarios = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0555-') as tmp:
        base = Path(tmp)
        base.chmod(0o700)
        target = base / 'evidence.json'
        target.write_text('{"safe": true}', encoding='utf-8')
        target.chmod(0o600)
        assert secure_json(target) == {'safe': True}
        scenarios.append('valid-descriptor')

        def rejected(label, path):
            try:
                secure_json(path)
            except (ValueError, OSError):
                scenarios.append(label)
            else:
                raise AssertionError(label + ' accepted')

        link = base / 'link.json'
        link.symlink_to(target)
        rejected('symlink-rejected', link)
        target.chmod(0o644)
        rejected('unsafe-permissions-rejected', target)
        target.chmod(0o600)
        target.write_bytes(b' ' * 1048577)
        rejected('oversized-rejected', target)
        target.write_text('{}', encoding='utf-8')
        rejected('directory-rejected', base)
        hardlink = base / 'hardlink.json'
        os.link(target, hardlink)
        rejected('hardlink-rejected', target)
        hardlink.unlink()
        rejected('missing-rejected', base / 'missing.json')
    return {'version': '0.5.55', 'selftest': 'passed', 'scenarios': scenarios,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


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



def classify_stores(bundle=None, journal=None, reservation=None):
    """Pure comparison of caller-supplied summaries, never an atomic snapshot."""
    sources = {'bundle': bundle, 'journal': journal, 'reservation': reservation}
    ids = []
    present = []
    for name, item in sources.items():
        if item is None:
            continue
        if not isinstance(item, dict):
            raise ValueError('Invalid ' + name + ' summary')
        if name == 'bundle' and item.get('phase') == 'idle' and 'transactionId' not in item:
            continue
        if name == 'reservation' and item.get('phase') == 'empty' and 'transactionId' not in item:
            continue
        token = item.get('transactionId')
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('Invalid ' + name + ' transaction identity')
        if not isinstance(item.get('phase'), str) or not item['phase']:
            raise ValueError('Invalid ' + name + ' phase')
        if name == 'bundle' and item.get('integrityVerified') is not True:
            raise ValueError('Unverified bundle integrity')
        if name == 'journal' and (type(item.get('historyLength')) is not int or item['historyLength'] < 1):
            raise ValueError('Unverified journal history')
        if name == 'reservation' and item.get('phase') != 'reserved':
            raise ValueError('Unknown reservation phase')
        for key in ('executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified',
                    'finalizationAuthorized', 'reservationReusable'):
            if key in item and item[key] is not False:
                raise ValueError('Unsafe ' + name + ' flag: ' + key)
        if 'commandsExecuted' in item and (type(item['commandsExecuted']) is not int or item['commandsExecuted'] != 0):
            raise ValueError('Unsafe ' + name + ' command count')
        ids.append(token)
        present.append(name)
    if not present:
        assessment = 'empty'
    elif len(set(ids)) > 1:
        assessment = 'identity-conflict'
    elif len(present) < 3:
        assessment = 'incomplete'
    else:
        assessment = 'consistent-observable'
    return {'version': '0.5.53', 'mode': 'non-atomic-cross-store-diagnostic',
            'assessment': assessment, 'observedStores': present,
            'transactionId': ids[0] if ids and len(set(ids)) == 1 else None,
            'atomicCrossStoreSnapshot': False, 'evidenceAuthenticated': False,
            'executionAuthorized': False, 'finalizationAuthorized': False,
            'reservationReusable': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def inspect_stores(bundle_root=None, journal_root=None, reservation_root=None):
    """Sequential read-only inspection; all roots must be explicit and isolated."""
    if any(x is None for x in (bundle_root, journal_root, reservation_root)):
        raise ValueError('All three explicit roots required')
    base = Path(reservation_root)
    if base.is_symlink() or not base.is_dir() or base.stat().st_uid != os.getuid() or base.stat().st_mode & 0o077:
        raise ValueError('Unsafe reservation root')
    b = inspect(bundle_root=bundle_root)['bundle']
    j = inspect(journal_root=journal_root)['journal']
    rpath = base / 'coordination-rehearsal-v0543.json'
    if not rpath.exists() and not rpath.is_symlink():
        r = None
    else:
        r = secure_json(rpath)
        if (r.get('version') != '0.5.43' or r.get('mode') != 'isolated-rehearsal-only'
                or r.get('phase') != 'reserved' or r.get('executionAuthorized') is not False
                or r.get('physicalExecutionEnabled') is not False or r.get('commandsExecuted') != 0):
            raise ValueError('Invalid reservation format')
    return classify_stores(b, j, r)



def observe_stores_twice(bundle_root=None, journal_root=None, reservation_root=None, reader=None):
    """Compare two sequential validated observations and raw evidence fingerprints.

    Diagnostic only: no atomicity, no locks, no execution authorization. A
    transient change reverted between observations may remain undetected.
    """
    denied = {'version': '0.5.54', 'mode': 'read-only-double-observation',
              'atomicCrossStoreSnapshot': False, 'evidenceAuthenticated': False,
              'executionAuthorized': False, 'finalizationAuthorized': False,
              'reservationReusable': False, 'physicalExecutionEnabled': False,
              'physicalRollbackVerified': False, 'commandsExecuted': 0}
    if any(root is None for root in (bundle_root, journal_root, reservation_root)):
        return {**denied, 'assessment': 'observation-failed', 'observationError': 'missing-root'}

    paths = (Path(bundle_root) / 'serpantinum-display-v0513' / 'bundle.json',
             Path(journal_root) / JOURNAL,
             Path(reservation_root) / 'coordination-rehearsal-v0543.json')

    def capture():
        # The existing contract validates schemas and safety flags. Fingerprints
        # additionally detect changes in fields omitted from its summaries.
        summary = inspect_stores(bundle_root, journal_root, reservation_root)
        fingerprints = []
        for path in paths:
            if not path.exists() and not path.is_symlink():
                fingerprints.append(None)
                continue
            data = secure_json(path)
            canonical = json.dumps(data, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False).encode('utf-8')
            fingerprints.append(hashlib.sha256(canonical).hexdigest())
        return summary, tuple(fingerprints)

    try:
        take = reader if reader is not None else capture
        first = take()
        second = take()
        if (not isinstance(first, tuple) or not isinstance(second, tuple)
                or len(first) != 2 or len(second) != 2):
            raise ValueError('Invalid observation')
        if first != second:
            return {**denied, 'assessment': 'concurrent-change-detected',
                    'observationError': None}
        if not isinstance(first[0], dict):
            raise ValueError('Invalid observation summary')
        return {**denied, 'assessment': 'stable-observation',
                'underlyingAssessment': first[0].get('assessment'),
                'observationError': None}
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
        return {**denied, 'assessment': 'observation-failed',
                'observationError': 'read-or-validation-failed'}


def selftest_observation():
    import tempfile
    token = 'a' * 24
    summary = classify_stores()
    stable = (summary, (None, None, None))
    scenarios = []
    def test(label, values, expected):
        iterator = iter(values)
        out = observe_stores_twice('/unused', '/unused', '/unused', reader=lambda: next(iterator))
        assert out['assessment'] == expected and out['executionAuthorized'] is False
        assert out['atomicCrossStoreSnapshot'] is False
        scenarios.append(label)
    test('stable', [stable, stable], 'stable-observation')
    test('changed-phase', [stable, ({**summary, 'assessment': 'incomplete'}, stable[1])], 'concurrent-change-detected')
    test('changed-evidence-content', [stable, (summary, ('different', None, None))], 'concurrent-change-detected')
    test('changed-identity', [stable, ({**summary, 'transactionId': token}, stable[1])], 'concurrent-change-detected')
    def failing():
        raise ValueError('injected read failure')
    out = observe_stores_twice('/unused', '/unused', '/unused', reader=failing)
    assert out['assessment'] == 'observation-failed'; scenarios.append('read-failure')
    out = observe_stores_twice()
    assert out['assessment'] == 'observation-failed'; scenarios.append('missing-roots')
    # Real file observations: a completely absent isolated bundle/reservation
    # cannot mask a missing journal, and must fail closed.
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0554-') as tmp:
        root = Path(tmp)
        root.chmod(0o700)
        out = observe_stores_twice(root, root, root)
        assert out['assessment'] == 'observation-failed'
        scenarios.append('missing-journal-rejected')
    return {'version': '0.5.54', 'selftest': 'passed', 'scenarios': scenarios,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


def selftest_stores():
    token = 'a' * 24
    b = {'phase': 'sealed-read-only', 'transactionId': token, 'integrityVerified': True}
    j = {'phase': 'prepared', 'transactionId': token, 'historyLength': 1}
    r = {'phase': 'reserved', 'transactionId': token, 'executionAuthorized': False,
         'physicalExecutionEnabled': False, 'commandsExecuted': 0}
    scenarios = []
    assert classify_stores()['assessment'] == 'empty'; scenarios.append('empty')
    assert classify_stores(b)['assessment'] == 'incomplete'; scenarios.append('incomplete')
    assert classify_stores(b, j, r)['assessment'] == 'consistent-observable'; scenarios.append('consistent-observable')
    assert classify_stores(b, j, dict(r, transactionId='b'*24))['assessment'] == 'identity-conflict'; scenarios.append('identity-conflict')
    for label, args in (
        ('bundle-integrity-rejected', (dict(b, integrityVerified=False), j, r)),
        ('journal-history-rejected', (b, dict(j, historyLength=0), r)),
        ('reservation-flags-rejected', (b, j, dict(r, executionAuthorized=True))),
        ('invalid-identity-rejected', (dict(b, transactionId='invalid'), j, r)),
    ):
        try:
            classify_stores(*args)
        except ValueError:
            scenarios.append(label)
        else:
            raise AssertionError(label)
    return {'version': '0.5.53', 'selftest': 'passed', 'scenarios': scenarios,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


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
    parser.add_argument('command', choices=['inspect', 'selftest', 'inspect-stores', 'selftest-stores', 'observe-stores', 'selftest-observation', 'selftest-secure-json'])
    parser.add_argument('--bundle-root', type=Path)
    parser.add_argument('--journal-root', type=Path)
    parser.add_argument('--reservation-root', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'selftest':
            data = selftest()
        elif args.command == 'selftest-stores':
            data = selftest_stores()
        elif args.command == 'selftest-secure-json':
            data = selftest_secure_json()
        elif args.command == 'selftest-observation':
            data = selftest_observation()
        elif args.command == 'observe-stores':
            data = observe_stores_twice(args.bundle_root, args.journal_root, args.reservation_root)
        elif args.command == 'inspect-stores':
            data = inspect_stores(args.bundle_root, args.journal_root, args.reservation_root)
        else:
            data = inspect(args.bundle_root, args.journal_root)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
