#!/usr/bin/env python3
"""Serpantinum v0.5.38: read-only consistency audit of isolated v0.5.37 evidence.

No physical execution, no systemd query, no modification to evidence. Hashes are
unkeyed consistency checks, NOT authentication against a malicious local editor.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
HISTORY = ['prepared', 'supervised', 'operation-reserved', 'operation-interrupted',
           'deadline-expired', 'rollback-running']
FILES = ('recovery-protocol-v0531.json', 'snapshot-seal-v0537.json', 'observation-v0537.json')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def secure_read(folder, name):
    path = folder / name
    if path.is_symlink():
        raise ValueError('Unsafe symlink: ' + name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > 1024 * 1024:
            raise ValueError('Unsafe evidence file: ' + name)
        raw = os.read(fd, 1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Evidence too large: ' + name)
        return json.loads(raw.decode('utf-8'))
    finally:
        os.close(fd)


def inspect(root, token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    folder = Path(root)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Invalid isolated root')
    st = folder.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('Unsafe isolated root')
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime:
        live = (Path(runtime) / 'serpantinum-display-v0513').resolve()
        resolved = folder.resolve()
        if resolved in (live, Path(runtime).resolve()) or live in resolved.parents:
            raise ValueError('Live bundle access forbidden')
    # Share the exact lock used by the existing persistent protocol.
    spec = importlib.util.spec_from_file_location('bundle_0538', HERE / 'display-recovery-bundle.py')
    if spec is None or spec.loader is None:
        raise ValueError('Bundle lock dependency unavailable')
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    with bundle.locked(folder):
        journal, seal, receipt = [secure_read(folder, name) for name in FILES]
        if not all(isinstance(v, dict) for v in (journal, seal, receipt)):
            raise ValueError('Invalid evidence structure')
        state = journal.get('state')
        if journal.get('version') != '0.5.31' or journal.get('mode') != 'isolated-simulation-only' or not isinstance(state, dict):
            raise ValueError('Invalid protocol journal')
        if state.get('transactionId') != token or state.get('phase') != 'rollback-running' or state.get('history') != HISTORY:
            raise ValueError('Unexpected protocol history or identity')
        if state.get('physicalExecutionEnabled') is not False or state.get('physicalRollbackVerified') is not False or type(state.get('commandsExecuted')) is not int or state['commandsExecuted'] != 0:
            raise ValueError('Unsafe protocol execution flags')
        deadline = state.get('deadline')
        if type(deadline) not in (int, float) or not (0 < deadline < float('inf')):
            raise ValueError('Invalid deadline')
        initial = {'version': '0.5.31', 'mode': 'isolated-simulation-only',
                   'state': {'transactionId': token, 'deadline': deadline, 'phase': 'prepared',
                             'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
                             'commandsExecuted': 0, 'history': ['prepared']}}
        if seal.get('version') != '0.5.37' or seal.get('transactionId') != token:
            raise ValueError('Seal identity mismatch')
        snapshot = seal.get('snapshot')
        if not isinstance(snapshot, dict) or snapshot.get('schemaVersion') != 1 or not isinstance(snapshot.get('outputs'), list) or not 1 <= len(snapshot['outputs']) <= 16:
            raise ValueError('Invalid sealed snapshot')
        names = [o.get('name') if isinstance(o, dict) else None for o in snapshot['outputs']]
        if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
            raise ValueError('Invalid output identities')
        if seal.get('snapshotSha256') != digest(snapshot) or seal.get('initialJournalSha256') != digest(initial):
            raise ValueError('Seal content hash mismatch')
        if seal.get('physicalRollbackVerified') is not False or seal.get('commandsExecuted') != 0:
            raise ValueError('Unsafe seal flags')
        if receipt.get('version') != '0.5.37' or receipt.get('mode') != 'isolated-read-only-observation' or receipt.get('transactionId') != token:
            raise ValueError('Receipt identity mismatch')
        if receipt.get('snapshotSha256') != seal['snapshotSha256'] or receipt.get('initialJournalSha256') != seal['initialJournalSha256']:
            raise ValueError('Receipt/seal hash mismatch')
        if receipt.get('assessment') not in ('observable-equivalent', 'observable-different', 'observation-failed'):
            raise ValueError('Invalid observation assessment')
        if receipt.get('journalUnchanged') is not True or receipt.get('physicalRollbackAttempted') is not False or receipt.get('physicalRollbackVerified') is not False or receipt.get('automaticRetryAuthorized') is not False or type(receipt.get('commandsExecuted')) is not int or receipt['commandsExecuted'] != 0:
            raise ValueError('Unsafe receipt flags')
        if not isinstance(receipt.get('differences'), list):
            raise ValueError('Invalid observation differences')
        if receipt['assessment'] == 'observable-equivalent' and (receipt['differences'] or receipt.get('observationError') is not None):
            raise ValueError('Contradictory equivalent assessment')
        if receipt['assessment'] == 'observation-failed' and not isinstance(receipt.get('observationError'), str):
            raise ValueError('Missing observation failure evidence')
        return {'version': '0.5.38', 'mode': 'isolated-read-only-evidence-audit',
                'transactionId': token, 'assessment': receipt['assessment'], 'consistent': True,
                'historyValidated': True, 'snapshotContentHashVerified': True,
                'initialJournalHashVerified': True, 'receiptReferencesVerified': True,
                'outputs': names, 'journalPhase': state['phase'],
                'physicalRollbackVerified': False, 'physicalExecutionEnabled': False,
                'commandsExecuted': 0, 'liveBundleTouched': False,
                'note': 'Consistency only; no independent proof of physical rollback or authenticity'}


def selftest():
    token = 'a' * 24
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0538-') as root:
        folder = Path(root)
        os.chmod(folder, 0o700)
        snap = {'schemaVersion': 1, 'outputs': [{'name': 'eDP-1', 'width': 1920, 'height': 1080}]}
        initial = {'version': '0.5.31', 'mode': 'isolated-simulation-only', 'state': {
            'transactionId': token, 'deadline': 15.0, 'phase': 'prepared', 'history': ['prepared'],
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False, 'commandsExecuted': 0}}
        journal = json.loads(json.dumps(initial))
        journal['state']['history'] = HISTORY[:]
        journal['state']['phase'] = 'rollback-running'
        seal = {'version': '0.5.37', 'transactionId': token, 'snapshot': snap,
                'snapshotSha256': digest(snap), 'initialJournalSha256': digest(initial),
                'physicalRollbackVerified': False, 'commandsExecuted': 0}
        receipt = {'version': '0.5.37', 'mode': 'isolated-read-only-observation', 'transactionId': token,
                   'assessment': 'observable-equivalent', 'differences': [], 'observationError': None,
                   'snapshotSha256': seal['snapshotSha256'], 'initialJournalSha256': seal['initialJournalSha256'],
                   'journalUnchanged': True, 'physicalRollbackAttempted': False,
                   'physicalRollbackVerified': False, 'automaticRetryAuthorized': False, 'commandsExecuted': 0}
        originals = (journal, seal, receipt)
        def write_all(values):
            for name, value in zip(FILES, values):
                path = folder / name
                path.write_bytes(canonical(value))
                os.chmod(path, 0o600)
        write_all(originals)
        assert inspect(root, token)['consistent']
        scenarios = [
            (0, lambda d: d['state']['history'].insert(2, 'supervised')),
            (0, lambda d: d['state'].update(transactionId='b' * 24)),
            (1, lambda d: d['snapshot']['outputs'][0].update(width=123)),
            (1, lambda d: d.update(initialJournalSha256='0' * 64)),
            (2, lambda d: d.update(snapshotSha256='0' * 64)),
            (2, lambda d: d.update(physicalRollbackVerified=True)),
            (2, lambda d: d.update(assessment='observable-equivalent', differences=['changed'])),
        ]
        for index, mutate in scenarios:
            data = json.loads(json.dumps(originals))
            mutate(data[index])
            write_all(data)
            try:
                inspect(root, token)
            except ValueError:
                pass
            else:
                raise AssertionError('Tampering accepted')
        write_all(originals)
        assert inspect(root, token)['consistent']
        return {'selftest': 'passed', 'tamperingScenariosRejected': len(scenarios),
                'commandsExecuted': 0, 'physicalRollbackVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'selftest'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--token')
    args = parser.parse_args()
    result = selftest() if args.command == 'selftest' else inspect(args.root, args.token)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'consistent': False, 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
