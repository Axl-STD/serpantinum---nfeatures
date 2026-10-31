#!/usr/bin/env python3
"""v0.5.47: diagnostic-only cross-evidence consistency. No writes or authorization."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect(root, token, injected=None):
    """Injected attestations are exclusively for offline tests, never CLI evidence."""
    link = load('display-evidence-link-v0546.py', 'link_0547')
    reservation = load('display-coordination-rehearsal.py', 'reserve_0547')
    lock = load('display-global-lock.py', 'lock_0547')
    if not isinstance(token, str) or not reservation.TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction identity')
    # The v0.5.46 linkage validates the reservation and receipt under its own lock.
    linked = link.link(root, token, injected)
    phase = linked['attestationPhase']
    retained = linked['retainedTestUnitVerified']
    if phase in ('unit-unloaded-unverifiable', 'query-failed', 'incomplete-response', 'unverified'):
        assessment = 'evidence-missing-or-unverifiable'
    elif phase == 'rejected':
        assessment = 'evidence-contradictory'
    elif phase == 'retained-oneshot-exited' and retained:
        assessment = 'retained-test-unit-observed-only'
    else:
        assessment = 'evidence-contradictory'
    return {'version': '0.5.47', 'mode': 'read-only-cross-evidence-audit',
            'transactionId': token, 'assessment': assessment,
            'attestationSource': linked['attestationSource'],
            'attestationPhase': phase, 'diagnosticReceiptPresent': linked['diagnosticReceiptPresent'],
            'reservationUnchanged': linked['reservationUnchanged'],
            'historicalIdentityVerified': False, 'operationCompletionVerified': False,
            'workerIdentityVerified': False, 'finalizationAuthorized': False,
            'reservationReusable': False, 'executionAuthorized': False,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0, 'supervisorsIntegrated': False}


def selftest():
    reserve = load('display-coordination-rehearsal.py', 'reserve_test_0547')
    token = 'a' * 24
    base = {'transactionId': token, 'phase': 'unit-unloaded-unverifiable',
            'retainedTestUnitVerified': False, 'executionAuthorized': False,
            'finalizationAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0547-') as root:
        os.chmod(root, 0o700)
        reserve.operate(root, token, 'reserve')
        path = Path(root) / reserve.RECEIPT
        original = path.read_bytes()
        cases = [('missing', base, 'evidence-missing-or-unverifiable'),
                 ('contradictory', dict(base, phase='rejected'), 'evidence-contradictory'),
                 ('retained', dict(base, phase='retained-oneshot-exited', retainedTestUnitVerified=True),
                  'retained-test-unit-observed-only')]
        for label, data, expected in cases:
            report = inspect(root, token, data)
            assert report['assessment'] == expected, label
            assert report['finalizationAuthorized'] is False
        for label, bad_token, data in [
            ('wrong-reservation', 'b'*24, base),
            ('wrong-attestation', token, dict(base, transactionId='b'*24)),
            ('unsafe-flags', token, dict(base, executionAuthorized=True))]:
            try:
                inspect(root, bad_token, data)
            except ValueError:
                pass
            else:
                raise AssertionError(label + ' accepted')
        assert path.read_bytes() == original
        assert not (Path(root) / 'coordination-finalization-v0544.json').exists()
    return {'version': '0.5.47', 'selftest': 'passed',
            'scenarios': ['missing-distinguished', 'contradictory-distinguished',
                          'retained-not-authorized', 'wrong-reservation-rejected',
                          'wrong-attestation-rejected', 'unsafe-flags-rejected',
                          'reservation-unchanged'], 'executionAuthorized': False,
            'commandsExecuted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'inspect'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--token')
    args = parser.parse_args()
    try:
        if args.command == 'selftest':
            result = selftest()
        elif args.root is None or args.token is None:
            raise ValueError('Explicit isolated root and token required')
        else:
            result = inspect(args.root, args.token)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if args.command == 'selftest' else 2
    except (ValueError, OSError, TypeError, KeyError, AssertionError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
