#!/usr/bin/env python3
"""v0.5.44 isolated diagnostic finalization; no worker attestation or reuse authorization.

Consumes a v0.5.43 reservation and an explicit injected test evidence file.
Writes a separate immutable diagnostic receipt. Never changes the reservation,
launches services, checks real workers, or changes monitor configuration.
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
RECEIPT = 'coordination-finalization-v0544.json'
TOKEN = re.compile(r'[0-9a-f]{24}\Z')


def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / name)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_file(path, limit=16384):
    if path.is_symlink():
        raise ValueError('Symlinked evidence')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > limit):
            raise ValueError('Unsafe evidence file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Oversized evidence')
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def finalize(root, token, evidence_path):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction token')
    lock = load('display-global-lock.py', 'lock_0544')
    reserve = load('display-coordination-rehearsal.py', 'reserve_0544')
    with lock.exclusive(root):
        folder = lock.private_root(root)
        old = reserve.safe_read(folder)
        if old is None or old['phase'] != 'reserved' or old['transactionId'] != token:
            raise ValueError('No matching reserved transaction')
        dest = folder / RECEIPT
        if dest.exists() or dest.is_symlink():
            raise ValueError('Finalization receipt already exists')
        evidence, digest = read_file(Path(evidence_path))
        if (not isinstance(evidence, dict) or evidence.get('schemaVersion') != 1 or
                evidence.get('mode') != 'injected-offline-test-only' or
                evidence.get('transactionId') != token or
                evidence.get('workerInactiveInjected') is not True or
                evidence.get('operationTerminalInjected') is not True or
                evidence.get('physicalExecutionEnabled') is not False or
                evidence.get('physicalRollbackVerified') is not False or
                evidence.get('commandsExecuted') != 0):
            raise ValueError('Incomplete or inconsistent injected evidence')
        receipt = {'version': '0.5.44', 'mode': 'isolated-diagnostic-finalization-only',
                   'phase': 'diagnostic-finalization-recorded', 'transactionId': token,
                   'injectedEvidenceSha256': digest, 'reservationUnchanged': True,
                   'workerIdentityVerified': False, 'workerInactivityVerified': False,
                   'operationCompletionVerified': False, 'executionAuthorized': False,
                   'reservationReusable': False, 'supervisorsIntegrated': False,
                   'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
                   'commandsExecuted': 0,
                   'note': 'Injected offline evidence only; no real worker verification or release'}
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(receipt, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        return receipt


def status(root):
    lock = load('display-global-lock.py', 'lock_status_0544')
    with lock.exclusive(root):
        folder = lock.private_root(root)
        path = folder / RECEIPT
        if not path.exists() and not path.is_symlink():
            return {'version': '0.5.44', 'phase': 'no-finalization', 'executionAuthorized': False}
        receipt, _ = read_file(path)
        if (not isinstance(receipt, dict) or receipt.get('version') != '0.5.44' or
                receipt.get('phase') != 'diagnostic-finalization-recorded' or
                receipt.get('executionAuthorized') is not False or
                receipt.get('reservationReusable') is not False):
            raise ValueError('Invalid finalization receipt')
        return receipt


def write_evidence(root, token, **overrides):
    data = {'schemaVersion': 1, 'mode': 'injected-offline-test-only',
            'transactionId': token, 'workerInactiveInjected': True,
            'operationTerminalInjected': True, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}
    data.update(overrides)
    path = Path(root) / 'injected-evidence.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    path.chmod(0o600)
    return path


def selftest():
    reserve = load('display-coordination-rehearsal.py', 'reserve_test_0544')
    token = 'a' * 24
    cases = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0544-') as tmp:
        os.chmod(tmp, 0o700)
        reserve.operate(tmp, token, 'reserve')
        evidence = write_evidence(tmp, token)
        before = (Path(tmp) / reserve.RECEIPT).read_bytes()
        for label, other_token, changes in (
            ('wrong-identity', 'b'*24, {}),
            ('active-worker-injected', token, {'workerInactiveInjected': False}),
            ('incomplete-evidence', token, {'operationTerminalInjected': False}),
            ('physical-flag-rejected', token, {'physicalExecutionEnabled': True}),
        ):
            write_evidence(tmp, token, **changes)
            try:
                finalize(tmp, other_token, evidence)
            except ValueError:
                cases.append(label)
            else:
                raise AssertionError(label + ' accepted')
        write_evidence(tmp, token)
        receipt = finalize(tmp, token, evidence)
        assert receipt['reservationReusable'] is False
        assert status(tmp) == receipt
        assert (Path(tmp) / reserve.RECEIPT).read_bytes() == before
        cases.append('reservation-unchanged')
        try:
            finalize(tmp, token, evidence)
        except ValueError:
            cases.append('duplicate-rejected')
        else:
            raise AssertionError('Duplicate accepted')
        try:
            reserve.operate(tmp, 'b'*24, 'reserve')
        except ValueError:
            cases.append('reuse-rejected')
        else:
            raise AssertionError('Reuse accepted')
        assert status(tmp)['phase'] == 'diagnostic-finalization-recorded'
    return {'version': '0.5.44', 'selftest': 'passed', 'scenarios': cases,
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'commandsExecuted': 0, 'workerInactivityVerified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('selftest', 'finalize', 'status'))
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    p.add_argument('--evidence', type=Path)
    args = p.parse_args()
    try:
        if args.command == 'selftest':
            data = selftest()
        elif args.root is None:
            raise ValueError('Explicit isolated root required')
        elif args.command == 'status':
            data = status(args.root)
        elif args.token is None or args.evidence is None:
            raise ValueError('Token and injected evidence file required')
        else:
            data = finalize(args.root, args.token, args.evidence)
        print(json.dumps(data, indent=2))
        return 0
    except (ValueError, OSError, AssertionError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
