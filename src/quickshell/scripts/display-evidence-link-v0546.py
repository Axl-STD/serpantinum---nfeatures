#!/usr/bin/env python3
"""v0.5.46 read-only evidence linkage. Never finalizes, releases, or authorizes execution."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import os

HERE = Path(__file__).resolve().parent


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def link(root, token, attestation=None):
    lock = load('display-global-lock.py', 'lock_0546')
    reservation = load('display-coordination-rehearsal.py', 'reserve_0546')
    finalization = load('display-coordination-finalization.py', 'finish_0546')
    worker = load('display-worker-attestation.py', 'worker_0546')
    if not isinstance(token, str) or not reservation.TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    with lock.exclusive(root):
        folder = lock.private_root(root)
        saved = reservation.safe_read(folder)
        if saved is None or saved.get('phase') != 'reserved' or saved.get('transactionId') != token:
            raise ValueError('Reservation missing, mismatched or non-reserved')
        receipt_path = folder / finalization.RECEIPT
        if receipt_path.is_symlink():
            raise ValueError('Symlinked finalization receipt')
        receipt = None
        if receipt_path.exists():
            receipt, _ = finalization.read_file(receipt_path)
            if not isinstance(receipt, dict) or receipt.get('transactionId') != token:
                raise ValueError('Mismatched finalization receipt')
            if (receipt.get('version') != '0.5.44' or
                    receipt.get('phase') != 'diagnostic-finalization-recorded' or
                    receipt.get('executionAuthorized') is not False or
                    receipt.get('reservationReusable') is not False):
                raise ValueError('Invalid finalization receipt')
        # The injected attestation is only for offline selftests, never accepted as live evidence.
        if attestation is None:
            observed = worker.probe(token)
            source = 'live-read-only-probe'
        else:
            observed = attestation
            source = 'injected-offline-test-only'
        if not isinstance(observed, dict) or observed.get('transactionId') != token:
            raise ValueError('Attestation identity mismatch')
        if (observed.get('executionAuthorized') is not False or
                observed.get('finalizationAuthorized') is not False or
                observed.get('physicalExecutionEnabled') is not False or
                observed.get('physicalRollbackVerified') is not False or
                observed.get('commandsExecuted') != 0):
            raise ValueError('Unsafe attestation')
        return {'version': '0.5.46', 'mode': 'read-only-evidence-link',
                'phase': 'diagnostic-only', 'transactionId': token,
                'reservationPhase': saved['phase'],
                'attestationSource': source, 'attestationPhase': observed.get('phase'),
                'retainedTestUnitVerified': observed.get('retainedTestUnitVerified') is True,
                'historicalIdentityVerified': False, 'operationCompletionVerified': False,
                'diagnosticReceiptPresent': receipt is not None,
                'reservationUnchanged': True, 'finalizationAuthorized': False,
                'reservationReusable': False, 'executionAuthorized': False,
                'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
                'commandsExecuted': 0, 'supervisorsIntegrated': False}


def selftest():
    reserve = load('display-coordination-rehearsal.py', 'reserve_test_0546')
    worker = load('display-worker-attestation.py', 'worker_test_0546')
    token = 'a' * 24
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0546-') as root:
        os.chmod(root, 0o700)
        reserve.operate(root, token, 'reserve')
        before = (Path(root) / reserve.RECEIPT).read_bytes()
        base = {'transactionId': token, 'phase': 'query-failed', 'retainedTestUnitVerified': False, 'executionAuthorized': False, 'finalizationAuthorized': False, 'physicalExecutionEnabled': False, 'physicalRollbackVerified': False, 'commandsExecuted': 0}
        out = link(root, token, base)
        assert out['attestationPhase'] == 'query-failed'
        assert not out['finalizationAuthorized']
        try:
            link(root, 'b'*24, base)
            raise AssertionError('Wrong identity accepted')
        except ValueError:
            pass
        try:
            link(root, token, dict(base, executionAuthorized=True))
            raise AssertionError('Unsafe flag accepted')
        except ValueError:
            pass
        try:
            link(root, token, dict(base, transactionId='b'*24))
            raise AssertionError('Wrong attestation identity accepted')
        except ValueError:
            pass
        assert (Path(root) / reserve.RECEIPT).read_bytes() == before
    return {'version': '0.5.46', 'selftest': 'passed',
            'scenarios': ['query-failure-fail-closed', 'wrong-reservation-rejected',
                          'unsafe-attestation-rejected', 'wrong-attestation-rejected',
                          'reservation-unchanged'],
            'executionAuthorized': False, 'commandsExecuted': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('selftest', 'inspect'))
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    a = p.parse_args()
    try:
        if a.command == 'selftest':
            result = selftest()
        elif a.root is None or a.token is None:
            raise ValueError('Explicit root and token required')
        else:
            result = link(a.root, a.token)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if a.command == 'selftest' else 2  # Diagnostic-only, never a success authorization.
    except (ValueError, OSError, TypeError, KeyError, AssertionError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
