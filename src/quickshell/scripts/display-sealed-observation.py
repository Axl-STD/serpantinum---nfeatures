#!/usr/bin/env python3
"""Serpantinum v0.5.37: isolated sealed snapshot + read-only observation.

Never changes displays, live bundle, persistent configuration, or systemd units.
The systemd probe is optional but required for 'observe'; only a previously
created, retained v0.5.36.1 test unit is accepted. This is diagnostic evidence,
not a physical recovery authorization or proof of worker-process identity.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SEAL = 'snapshot-seal-v0537.json'
RECEIPT = 'observation-v0537.json'


def load(filename):
    spec = importlib.util.spec_from_file_location('v0537_' + filename.replace('-', '_'), HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def snapshot():
    transaction = load('display-transaction.py')
    return {'schemaVersion': 1, 'outputs': transaction.normalize(transaction.monitors())}


def validate_snapshot(data):
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise ValueError('Invalid snapshot schema')
    outputs = data.get('outputs')
    if not isinstance(outputs, list) or not outputs or len(outputs) > 16:
        raise ValueError('Invalid snapshot outputs')
    verifier = load('display-restore-verify.py')
    if len(verifier.indexed(outputs)) != len(outputs):
        raise ValueError('Duplicate outputs')
    if not all(isinstance(o.get('name'), str) and o.get('name') for o in outputs):
        raise ValueError('Invalid output identity')
    return outputs


def digest(bundle, data):
    return hashlib.sha256(bundle.canonical(data)).hexdigest()


def prepare(root, token, captured=None):
    persistence = load('display-recovery-persistence.py')
    bundle = load('display-recovery-bundle.py')
    folder = persistence.directory(root)
    with bundle.locked(folder):
        journal = persistence.read(folder)
        if journal is None or journal['state'].get('transactionId') != token or journal['state'].get('phase') != 'prepared':
            raise ValueError('Snapshot must be sealed before operation')
        path = folder / SEAL
        if path.exists() or path.is_symlink():
            raise ValueError('Snapshot already sealed')
        data = snapshot() if captured is None else captured
        validate_snapshot(data)
        seal = {'version': '0.5.37', 'transactionId': token, 'snapshot': data,
                'snapshotSha256': digest(bundle, data), 'initialJournalSha256': digest(bundle, journal),
                'physicalRollbackVerified': False, 'commandsExecuted': 0}
        bundle.atomic_write(path, seal)
        return {'version': '0.5.37', 'phase': 'snapshot-sealed', 'transactionId': token,
                'outputs': [o['name'] for o in data['outputs']], 'physicalRollbackVerified': False,
                'commandsExecuted': 0}


def observe(root, token, observed=None, probe=None):
    persistence = load('display-recovery-persistence.py')
    bundle = load('display-recovery-bundle.py')
    verifier = load('display-restore-verify.py')
    folder = persistence.directory(root)
    with bundle.locked(folder):
        journal = persistence.read(folder)
        if journal is None or journal['state'].get('transactionId') != token or journal['state'].get('phase') != 'rollback-running':
            raise ValueError('Unbound or non-reconcilable transaction')
        path = folder / SEAL
        receipt = folder / RECEIPT
        if path.is_symlink() or not path.is_file() or receipt.exists() or receipt.is_symlink():
            raise ValueError('Missing seal or existing receipt')
        st = path.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > 1024 * 1024:
            raise ValueError('Unsafe seal')
        seal = json.loads(path.read_text(encoding='utf-8'))
        proto = load('display-recovery-protocol.py')
        initial = {'version': '0.5.31', 'mode': 'isolated-simulation-only',
                   'state': proto.begin(token, journal['state']['deadline'])}
        if seal.get('transactionId') != token or seal.get('initialJournalSha256') != digest(bundle, initial):
            raise ValueError('Seal/initial journal identity mismatch')
        if seal.get('snapshotSha256') != digest(bundle, seal.get('snapshot')):
            raise ValueError('Snapshot integrity mismatch')
        expected = validate_snapshot(seal['snapshot'])
        # Fail closed on missing/unknown systemd unit; this is a retained test unit contract.
        (probe or load('display-systemd-reconciliation.py').query_unit)(token)
        try:
            current = snapshot() if observed is None else observed
            actual = validate_snapshot(current)
            report = verifier.compare(expected, actual)
            assessment = 'observable-equivalent' if report['equivalentObservableState'] else 'observable-different'
            differences = report['differences']
            error = None
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            assessment, differences, error = 'observation-failed', [], str(exc)[:300]
        result = {'version': '0.5.37', 'mode': 'isolated-read-only-observation',
                  'transactionId': token, 'assessment': assessment, 'differences': differences,
                  'observationError': error, 'snapshotSha256': seal['snapshotSha256'],
                  'initialJournalSha256': seal['initialJournalSha256'], 'journalUnchanged': True,
                  'physicalRollbackAttempted': False, 'physicalRollbackVerified': False,
                  'commandsExecuted': 0, 'automaticRetryAuthorized': False,
                  'note': 'Observable comparison only; not proof of physical rollback'}
        bundle.atomic_write(receipt, result)
        return result


def selftest():
    persistence = load('display-recovery-persistence.py')
    fixture = {'schemaVersion': 1, 'outputs': [{'name': 'eDP-1', 'width': 1920, 'height': 1080,
               'x': 0, 'y': 0, 'scale': 1.0, 'transform': 0, 'disabled': False,
               'mirrorOf': 'none', 'refreshRate': 60.061}]}
    results = []
    for scenario in ('equivalent', 'different', 'observer-failure'):
        with tempfile.TemporaryDirectory(prefix='serpantinum-v0537-') as root:
            os.chmod(root, 0o700)
            token = secrets.token_hex(12)
            persistence.init(root, token, 15)
            prepare(root, token, fixture)
            for event, now in [('supervisor-ready', 1), ('reserve', 2), ('initiator-exited', 3), ('expire', 16), ('recover', 17)]:
                persistence.advance(root, token, event, now)
            before = persistence.status(root)
            if scenario == 'equivalent':
                current = fixture
            elif scenario == 'different':
                current = {'schemaVersion': 1, 'outputs': [dict(fixture['outputs'][0], x=100)]}
            else:
                current = {'schemaVersion': 2, 'outputs': fixture['outputs']}
            try:
                observe(root, token, current, probe=lambda _: (_ for _ in ()).throw(ValueError('active worker')))
                raise AssertionError('Unsafe worker accepted')
            except ValueError:
                pass
            result = observe(root, token, current, probe=lambda _: True)
            assert result['assessment'] == {'equivalent': 'observable-equivalent', 'different': 'observable-different',
                                             'observer-failure': 'observation-failed'}[scenario]
            assert persistence.status(root) == before
            try:
                observe(root, token, current, probe=lambda _: True)
                raise AssertionError('Duplicate receipt accepted')
            except ValueError:
                pass
            results.append(result['assessment'])
    return {'selftest': 'passed', 'scenarios': results, 'journalUnchanged': True,
            'systemdIntegration': 'not-run', 'commandsExecuted': 0, 'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'prepare', 'observe'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--token')
    args = parser.parse_args()
    if args.command == 'selftest':
        result = selftest()
    elif args.root is None or not isinstance(args.token, str) or len(args.token) != 24 or any(c not in '0123456789abcdef' for c in args.token):
        raise ValueError('Explicit isolated root and 24-character hex token required')
    elif args.command == 'prepare':
        result = prepare(args.root, args.token)
    else:
        result = observe(args.root, args.token)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get('assessment') not in ('observable-different', 'observation-failed') else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
