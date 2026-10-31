#!/usr/bin/env python3
"""Serpantinum v0.5.35: isolated read-only reconciliation of abandoned rehearsals.

Writes only a separate diagnostic receipt; never changes protocol state, starts
workers, executes monitor commands, or certifies physical rollback.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
RECEIPT = 'reconciliation-v0535.json'


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reconcile(root, token, expected, observer, worker_inactive):
    """Inject observer and worker-liveness proof; never trust a caller success flag."""
    persistence = load('display-recovery-persistence.py', 'persistence_0535')
    bundle = load('display-recovery-bundle.py', 'bundle_0535')
    verifier = load('display-restore-verify.py', 'verifier_0535')
    folder = persistence.directory(root)
    with bundle.locked(folder):
        envelope = persistence.read(folder)
        if envelope is None or envelope['state'].get('transactionId') != token:
            raise ValueError('Missing or mismatched transaction')
        state = envelope['state']
        if state.get('phase') != 'rollback-running':
            raise ValueError('Only abandoned rollback-running may be reconciled')
        if state.get('physicalExecutionEnabled') is not False or state.get('commandsExecuted') != 0:
            raise ValueError('Unsafe state')
        path = folder / RECEIPT
        if path.is_symlink() or path.exists():
            raise ValueError('Reconciliation evidence already exists')
        if worker_inactive() is not True:
            raise ValueError('Worker inactivity not verified')
        try:
            observed = observer()
            report = verifier.compare(expected, observed)
            assessment = 'observable-equivalent' if report['equivalentObservableState'] else 'observable-different'
            differences = report['differences']
            observation_error = None
        except Exception as exc:
            assessment = 'observation-failed'
            differences = []
            observation_error = str(exc)[:300]
        result = {'version': '0.5.35', 'mode': 'isolated-reconciliation-only',
                  'transactionId': token, 'sourcePhase': 'rollback-running',
                  'assessment': assessment, 'equivalentObservableState': assessment == 'observable-equivalent',
                  'differences': differences, 'observationError': observation_error,
                  'physicalExecutionEnabled': False, 'physicalRollbackAttempted': False,
                  'physicalRollbackVerified': False, 'commandsExecuted': 0,
                  'automaticRetryAuthorized': False,
                  'note': 'Diagnostic receipt only; original journal remains rollback-running'}
        bundle.atomic_write(path, result)
        return result


def selftest():
    persistence = load('display-recovery-persistence.py', 'persistence_test_0535')
    verifier = load('display-restore-verify.py', 'verify_test_0535')
    fixture = [{'name': 'eDP-1', 'width': 1920, 'height': 1080, 'x': 0, 'y': 0,
                'scale': 1.0, 'transform': 0, 'disabled': False,
                'mirrorOf': 'none', 'refreshRate': 60.061}]
    outcomes = []
    for kind in ('equivalent', 'different', 'observer-failure'):
        with tempfile.TemporaryDirectory(prefix='serpantinum-v0535-') as root:
            os.chmod(root, 0o700)
            token = 'a' * 24
            persistence.init(root, token, 15)
            for event, now in [('supervisor-ready', 1), ('reserve', 2), ('initiator-exited', 3),
                               ('expire', 16), ('recover', 17)]:
                persistence.advance(root, token, event, now)
            if kind == 'equivalent':
                observer = lambda: [dict(fixture[0])]
            elif kind == 'different':
                observer = lambda: [dict(fixture[0], x=100)]
            else:
                def observer():
                    raise RuntimeError('observer unavailable')
            try:
                reconcile(root, token, fixture, observer, lambda: False)
                raise AssertionError('Active worker accepted')
            except ValueError:
                pass
            try:
                reconcile(root, 'b' * 24, fixture, observer, lambda: True)
                raise AssertionError('Wrong token accepted')
            except ValueError:
                pass
            result = reconcile(root, token, fixture, observer, lambda: True)
            assert result['assessment'] == {'equivalent': 'observable-equivalent',
                                            'different': 'observable-different',
                                            'observer-failure': 'observation-failed'}[kind]
            assert persistence.status(root)['state']['phase'] == 'rollback-running'
            assert json.loads((Path(root) / RECEIPT).read_text()) == result
            try:
                reconcile(root, token, fixture, observer, lambda: True)
                raise AssertionError('Duplicate reconciliation accepted')
            except ValueError:
                pass
            outcomes.append(result['assessment'])
    assert verifier.compare(fixture, fixture)['equivalentObservableState']
    return {'selftest': 'passed', 'scenarios': outcomes,
            'wrongIdentityRejected': True, 'activeWorkerRejected': True,
            'duplicateRejected': True, 'journalUnchanged': True,
            'commandsExecuted': 0, 'physicalRollbackVerified': False,
            'systemdIntegration': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['selftest'])
    args = parser.parse_args()
    print(json.dumps(selftest(), indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
