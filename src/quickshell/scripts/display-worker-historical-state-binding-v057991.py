#!/usr/bin/env python3
"""v0.5.79.9.1: conservative read-only historical-state/orphan-evidence adapter.

Only retained isolated test-unit metadata can permit classification. This is
NOT independent worker authentication, finalization, or physical rollback proof.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SAFE_FALSE = ('historicalIdentityVerified', 'workerIdentityVerified',
              'operationCompletionVerified', 'finalizationAuthorized',
              'executionAuthorized', 'physicalExecutionEnabled',
              'physicalRollbackVerified')


def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / name)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect(root, token, metadata=None, *, metadata_probe=None):
    """Inspect isolated evidence; metadata is injected for offline testing only.

    A failed or missing probe always yields an unknown worker state. Metadata
    consistency permits classification, not a claim of authenticated identity.
    """
    result = dict(version='0.5.79.9.3', mode='read-only-historical-worker-state-binding',
                  assessment='worker-state-uncertain', workerState='unknown',
                  systemdExecutionMetadataConsistent=False,
                  receiptMetadataConsistent=False, automaticCleanup=False,
                  **{name: False for name in SAFE_FALSE}, commandsExecuted=0)
    orphan = load('display-worker-orphan-evidence-v05797.py', 'orphan_057991')
    historical = load('display-historical-worker-attestation-v05789.py', 'historical_057991')
    try:
        if not isinstance(token, str) or not orphan.TOKEN.fullmatch(token):
            raise ValueError('Invalid transaction token')
        if metadata is None:
            historical_result = (metadata_probe(token) if metadata_probe is not None
                                 else historical.probe(token))
        else:
            historical_result = historical.assess(token, metadata)
        if not isinstance(historical_result, dict):
            historical_result = {}
        safe_metadata = (historical_result.get('transactionId') == token
                         and historical_result.get('systemdExecutionMetadataConsistent') is True
                         and historical_result.get('assessment') == 'systemd-execution-metadata-consistent-only'
                         and all(historical_result.get(k) is False for k in SAFE_FALSE)
                         and historical_result.get('commandsExecuted') == 0)
        result['systemdExecutionMetadataConsistent'] = bool(safe_metadata)
        state = 'confirmed-exited' if safe_metadata else 'unknown'
        result['workerState'] = state
        classified = orphan.classify(root, token, state,
                                     expected_metadata=historical_result if safe_metadata else None)
        result['assessment'] = classified['assessment']
        result['receiptMetadataConsistent'] = (classified.get('receiptMetadataConsistent') is True
                                               and safe_metadata and result['assessment'] in
                                               ('unpublished-staged-evidence', 'published-with-staged-remnant'))
        result['stagedCount'] = classified['stagedCount']
        result['finalPresent'] = classified['finalPresent']
        if 'reason' in classified:
            result['reason'] = classified['reason']
        # A second independent observation is required only for live probes.
        # Offline injected metadata remains a single immutable test fixture.
        if metadata is None and safe_metadata:
            second = (metadata_probe(token) if metadata_probe is not None
                      else historical.probe(token))
            identity = ('transactionId', 'unit', 'invocationId', 'execMainPid',
                        'assessment', 'systemdExecutionMetadataConsistent')
            second_safe = (isinstance(second, dict)
                           and all(second.get(k) == historical_result.get(k) for k in identity)
                           and all(second.get(k) is False for k in SAFE_FALSE)
                           and second.get('commandsExecuted') == 0)
            if not second_safe:
                result['assessment'] = 'worker-state-uncertain'
                result['workerState'] = 'unknown'
                result['systemdExecutionMetadataConsistent'] = False
                result['receiptMetadataConsistent'] = False
                result['reason'] = 'Historical observations inconsistent'
                return result
        # Classification of bytes is not an authenticated binding to systemd.
        if not safe_metadata and result['assessment'] not in ('unsafe-file-entry', 'rejected'):
            result['assessment'] = 'worker-state-uncertain'
    except (ValueError, TypeError, OSError, KeyError) as exc:
        result['assessment'] = 'rejected'
        result['reason'] = str(exc)[:160]
    return result


def selftest():
    historical = load('display-historical-worker-attestation-v05789.py', 'historical_test_057991')
    reconciliation = load('display-systemd-reconciliation.py', 'reconciliation_test_057991')
    binding = load('display-worker-evidence-binding-v05791.py', 'binding_test_057991')
    import tempfile
    import os
    token = 'a' * 24
    base = dict(Id=reconciliation.unit_name(token), Description=reconciliation.MARKER + token,
                LoadState='loaded', ActiveState='active', SubState='exited',
                MainPID='0', ControlPID='0', Result='success', Type='oneshot',
                RemainAfterExit='yes', InvocationID='1' * 32, ExecMainPID='12345',
                ExecMainCode='1', ExecMainStatus='0', NRestarts='0',
                ExecMainStartTimestamp='Sat 2026-10-31 12:58:10 CST',
                ExecMainExitTimestamp='Sat 2026-10-31 12:58:10 CST')
    receipt = binding.canonical(binding.worker_receipt(token, '1' * 32, 12345))
    scenarios = [
        ('consistent-unpublished', base, True, 'unpublished-staged-evidence'),
        ('running-unit', {**base, 'SubState': 'running'}, True, 'worker-state-uncertain'),
        ('missing-unit', {**base, 'LoadState': 'not-found'}, True, 'worker-state-uncertain'),
        ('wrong-unit', {**base, 'Id': reconciliation.unit_name('b' * 24)}, True, 'worker-state-uncertain'),
        ('wrong-description', {**base, 'Description': 'wrong'}, True, 'worker-state-uncertain'),
        ('wrong-invocation', {**base, 'InvocationID': '0' * 32}, True, 'worker-state-uncertain'),
        ('wrong-pid', {**base, 'ExecMainPID': '0'}, True, 'worker-state-uncertain'),
        ('nonzero-result', {**base, 'ExecMainStatus': '1'}, True, 'worker-state-uncertain'),
        ('restarted-unit', {**base, 'NRestarts': '1'}, True, 'worker-state-uncertain'),
        ('missing-field', {k: v for k, v in base.items() if k != 'InvocationID'}, True, 'worker-state-uncertain'),
        ('consistent-empty', base, False, 'no-receipt-evidence'),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v057991-') as tmp:
        folder = Path(tmp)
        os.chmod(folder, 0o700)
        staged = folder / '.receipt-staged-abc'
        for name, props, with_staged, expected in scenarios:
            if with_staged:
                staged.write_bytes(receipt)
                staged.chmod(0o600)
            elif staged.exists():
                staged.unlink()
            before = staged.read_bytes() if staged.exists() else None
            out = inspect(folder, token, props)
            assert out['assessment'] == expected, (name, out)
            assert (staged.read_bytes() if staged.exists() else None) == before, name
            assert all(out[k] is False for k in SAFE_FALSE), name
            assert out['commandsExecuted'] == 0 and out['automaticCleanup'] is False, name
            results.append(dict(scenario=name, passed=True))
        staged.write_bytes(receipt)
        staged.chmod(0o600)
        for name, probe in [
            ('probe-query-failed', lambda _: historical.assess(token, None, 'query failed')),
            ('probe-missing-response', lambda _: None),
            ('probe-unsafe-flags', lambda _: {**historical.assess(token, base), 'executionAuthorized': True}),
        ]:
            out = inspect(folder, token, metadata_probe=probe)
            assert out['assessment'] == 'worker-state-uncertain', (name, out)
            assert all(out[k] is False for k in SAFE_FALSE), name
            results.append(dict(scenario=name, passed=True))
        out = inspect(folder, 'invalid', base)
        assert out['assessment'] == 'rejected'
        results.append(dict(scenario='invalid-token', passed=True))
    return dict(version='0.5.79.9.3', mode='offline-historical-worker-state-binding',
                selftest='passed', totalScenarios=len(results), results=results,
                productionModified=False, systemdIntegration=False,
                automaticCleanup=False, receiptMetadataConsistent=False,
                **{name: False for name in SAFE_FALSE}, commandsExecuted=0,
                limitations=['Injected historical metadata; no systemd unit launched',
                             'Consistent metadata is not authenticated worker identity',
                             'No physical rollback, power-loss durability, or atomic cross-store snapshot'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))


if __name__ == '__main__':
    sys.exit(main())
