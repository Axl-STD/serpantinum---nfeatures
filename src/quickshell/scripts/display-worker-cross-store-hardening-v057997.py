#!/usr/bin/env python3
"""v0.5.79.9.7: source-bound offline double-probe safety regression."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SAFE = ('historicalIdentityVerified', 'workerIdentityVerified',
        'operationCompletionVerified', 'finalizationAuthorized',
        'executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified')
TOKEN = 'a' * 24


def load():
    path = HERE / 'display-worker-historical-state-binding-v057991.py'
    spec = importlib.util.spec_from_file_location('adapter_v057997', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def metadata(inv='1'*32, pid=12345, consistent=True, unit='test-unit'):
    return dict(transactionId=TOKEN, unit=unit,
                assessment='systemd-execution-metadata-consistent-only' if consistent else 'rejected',
                systemdExecutionMetadataConsistent=consistent,
                invocationId=inv, execMainPid=pid, commandsExecuted=0,
                **{k: False for k in SAFE})


def scenario(first, second, classification='unpublished-staged-evidence',
             linked=True, expected='unpublished-staged-evidence', expected_calls=2):
    adapter = load()
    calls = []
    class Historical:
        @staticmethod
        def probe(token):
            assert token == TOKEN
            calls.append('probe')
            return first if len([c for c in calls if c == 'probe']) == 1 else second
    class Orphan:
        TOKEN = re.compile(r'[a-f0-9]{24}\Z')
        @staticmethod
        def classify(root, token, state, *, expected_metadata=None):
            calls.append('classify')
            assert root == '/isolated-fixture' and token == TOKEN
            return dict(assessment=classification if state == 'confirmed-exited' else 'worker-state-uncertain',
                        receiptMetadataConsistent=linked and state == 'confirmed-exited',
                        stagedCount=1, finalPresent=False)
    def fake_load(filename, alias):
        if filename == 'display-worker-orphan-evidence-v05797.py':
            return Orphan
        if filename == 'display-historical-worker-attestation-v05789.py':
            return Historical
        raise AssertionError(filename)
    adapter.load = fake_load
    out = adapter.inspect('/isolated-fixture', TOKEN)
    assert out['assessment'] == expected, (out, calls)
    assert calls.count('probe') == expected_calls, calls
    assert all(out[k] is False for k in SAFE) and out['commandsExecuted'] == 0
    assert out['automaticCleanup'] is False
    if expected == 'worker-state-uncertain':
        assert out['receiptMetadataConsistent'] is False
        assert out['workerState'] == 'unknown'
    return True


def selftest():
    good = metadata()
    cases = [
        ('stable', good, dict(good), 'unpublished-staged-evidence', 2),
        ('invocation-change', good, metadata(inv='2'*32), 'worker-state-uncertain', 2),
        ('pid-change', good, metadata(pid=54321), 'worker-state-uncertain', 2),
        ('unit-change', good, metadata(unit='other'), 'worker-state-uncertain', 2),
        ('unit-disappears', good, metadata(consistent=False), 'worker-state-uncertain', 2),
        ('second-missing', good, None, 'worker-state-uncertain', 2),
        ('second-unsafe', good, {**good, 'executionAuthorized': True}, 'worker-state-uncertain', 2),
        ('first-unsafe', metadata(consistent=False), good, 'worker-state-uncertain', 1),
    ]
    results = []
    for name, first, second, expected, count in cases:
        scenario(first, second, expected=expected, expected_calls=count)
        results.append(dict(scenario=name, passed=True))
    return dict(version='0.5.79.9.7', mode='offline-double-historical-observation',
                selftest='passed', totalScenarios=len(results), results=results,
                hardeningImplemented=True, atomicCrossStoreSnapshot=False,
                productionModified=False, systemdIntegration=False, automaticCleanup=False,
                receiptMetadataConsistent=False, **{k: False for k in SAFE},
                commandsExecuted=0,
                limitations=['Injected probes; no real systemd concurrency proof',
                             'Two observations are not an atomic cross-store snapshot'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))

if __name__ == '__main__':
    main()
