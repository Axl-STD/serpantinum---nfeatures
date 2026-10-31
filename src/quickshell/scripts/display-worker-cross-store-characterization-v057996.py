#!/usr/bin/env python3
"""v0.5.79.9.6: offline source-bound cross-store ordering characterization.

Injected callbacks only; never invokes systemctl or changes display configuration.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-worker-historical-state-binding-v057991.py'
TOKEN = 'a' * 24
SAFE = ('historicalIdentityVerified', 'workerIdentityVerified', 'operationCompletionVerified',
        'finalizationAuthorized', 'executionAuthorized', 'physicalExecutionEnabled',
        'physicalRollbackVerified')


def module():
    spec = importlib.util.spec_from_file_location('cross_store_source_v057996', SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError('Source adapter missing')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def metadata(inv='1' * 32, pid=12345, consistent=True):
    return dict(transactionId=TOKEN, assessment=('systemd-execution-metadata-consistent-only'
                 if consistent else 'rejected'), systemdExecutionMetadataConsistent=consistent,
                invocationId=inv, execMainPid=pid, commandsExecuted=0,
                **{k: False for k in SAFE})


def exercise(name, *, initial=None, later=None, receipt='stable', classification=None):
    adapter = module()
    events = []
    state = {'historical': initial if initial is not None else metadata(),
             'later': later, 'receipt': receipt}

    class Historical:
        @staticmethod
        def probe(token):
            assert token == TOKEN
            events.append('historical-probe')
            return dict(state['historical'])

    class Orphan:
        TOKEN = __import__('re').compile(r'[a-f0-9]{24}\Z')

        @staticmethod
        def classify(root, token, worker_state, *, expected_metadata=None):
            assert root == '/isolated-fixture' and token == TOKEN
            events.append('orphan-classify')
            if callable(classification):
                return classification(state, worker_state, expected_metadata)
            if worker_state != 'confirmed-exited':
                assessment, linked = 'worker-state-uncertain', False
            elif state['receipt'] == 'partial':
                assessment, linked = 'incomplete-staged-evidence', False
            elif state['receipt'] == 'changed':
                assessment, linked = 'conflicting-evidence', False
            elif state['receipt'] == 'missing':
                assessment, linked = 'no-receipt-evidence', False
            else:
                assessment, linked = 'unpublished-staged-evidence', True
            if state['later'] is not None:
                state['historical'] = state['later']
            return dict(assessment=assessment, receiptMetadataConsistent=linked,
                        stagedCount=0 if state['receipt'] == 'missing' else 1,
                        finalPresent=False)

    def fake_load(filename, alias):
        if filename == 'display-worker-orphan-evidence-v05797.py':
            return Orphan
        if filename == 'display-historical-worker-attestation-v05789.py':
            return Historical
        raise AssertionError('Unexpected dependency: ' + filename)

    adapter.load = fake_load
    out = adapter.inspect('/isolated-fixture', TOKEN)
    assert events == ['historical-probe', 'orphan-classify'], (name, events)
    assert out['commandsExecuted'] == 0 and out['automaticCleanup'] is False, name
    assert all(out[k] is False for k in SAFE), name
    return out, state


def selftest():
    cases = []
    def record(name, ok, finding=None):
        assert ok, name
        item = dict(scenario=name, passed=True)
        if finding:
            item['finding'] = finding
        cases.append(item)

    out, _ = exercise('stable')
    record('stable-consistent', out['receiptMetadataConsistent'] is True)
    out, state = exercise('invocation-change', later=metadata(inv='2' * 32))
    record('invocation-changes-after-probe', out['receiptMetadataConsistent'] is True and
           state['historical']['invocationId'] == '2' * 32, 'STALE_SYSTEMD_INVOCATION_EXPOSURE')
    out, state = exercise('pid-change', later=metadata(pid=54321))
    record('pid-changes-after-probe', out['receiptMetadataConsistent'] is True and
           state['historical']['execMainPid'] == 54321, 'STALE_SYSTEMD_PID_EXPOSURE')
    out, state = exercise('unit-disappears', later=metadata(consistent=False))
    record('unit-disappears-after-probe', out['receiptMetadataConsistent'] is True and
           state['historical']['systemdExecutionMetadataConsistent'] is False,
           'STALE_SYSTEMD_STATE_EXPOSURE')
    out, _ = exercise('partial', receipt='partial')
    record('partial-receipt-rejected', out['receiptMetadataConsistent'] is False)
    out, _ = exercise('changed', receipt='changed')
    record('changed-receipt-rejected', out['receiptMetadataConsistent'] is False)
    out, _ = exercise('missing', receipt='missing')
    record('missing-evidence-not-certified', out['receiptMetadataConsistent'] is False)
    out, _ = exercise('initial-invalid', initial=metadata(consistent=False))
    record('initial-unsafe-metadata-uncertain', out['assessment'] == 'worker-state-uncertain')
    findings = [item['finding'] for item in cases if 'finding' in item]
    return dict(version='0.5.79.9.6', mode='offline-source-bound-cross-store-characterization',
                selftest='passed', totalScenarios=len(cases), results=cases,
                findings=findings, hardeningComplete=False, productionModified=False,
                systemdIntegration=False, automaticCleanup=False,
                receiptMetadataConsistent=False, **{k: False for k in SAFE}, commandsExecuted=0,
                limitations=['Injected interleaving, not an observed systemd race',
                             'Mocked orphan classifier; source adapter callback order is exercised',
                             'No atomic cross-store snapshot, power-loss or physical rollback proof'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))


if __name__ == '__main__':
    main()
