#!/usr/bin/env python3
"""v0.5.79.9.9: isolated real-first-probe/adversarial-second-probe characterization.

Only creates an innocuous transient user systemd unit via the existing runtime
harness. Never changes display configuration or original worker receipts.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAFE = ('historicalIdentityVerified', 'workerIdentityVerified',
        'operationCompletionVerified', 'finalizationAuthorized',
        'executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified')


def load(name):
    path = HERE / name
    spec = importlib.util.spec_from_file_location('v057999_' + name.replace('-', '_').replace('.', '_'), path)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe(out):
    return (isinstance(out, dict) and out.get('commandsExecuted') == 0
            and out.get('automaticCleanup') is False
            and all(out.get(key) is False for key in SAFE))


def summary(results, runtime, real_probes):
    return dict(version='0.5.79.9.9',
                mode='isolated-real-first-adversarial-second-probe' if runtime else 'offline-adversarial-double-probe',
                selftest='passed' if results and all(x['passed'] for x in results) else 'failed',
                totalScenarios=len(results), results=results, realHistoricalProbeCalls=real_probes,
                systemdIntegration=runtime, productionModified=False, automaticCleanup=False,
                hardeningImplemented=True, atomicCrossStoreSnapshot=False,
                receiptMetadataConsistent=False, **{key: False for key in SAFE},
                commandsExecuted=0,
                limitations=['Second-probe changes are injected, not spontaneous systemd races',
                             'First probe uses real systemd only in run mode',
                             'No atomic snapshot, independent authentication, physical rollback or power-loss proof'])


def selftest():
    hardening = load('display-worker-cross-store-hardening-v057997.py')
    out = hardening.selftest()
    if out.get('selftest') != 'passed' or len(out.get('results', [])) != 8 or not safe(out):
        raise RuntimeError('Previous offline hardening regression failed')
    return summary([{'scenario': 'offline-' + x['scenario'], 'passed': x['passed']}
                    for x in out['results']], False, 0)


def adversarial(token, meta, root):
    adapter = load('display-worker-historical-state-binding-v057991.py')
    historical = load('display-historical-worker-attestation-v05789.py')
    if not meta.get('systemdExecutionMetadataConsistent'):
        raise RuntimeError('Real historical baseline unavailable')
    inv = 'f' * 32 if meta['invocationId'] != 'f' * 32 else 'e' * 32
    cases = [
        ('stable-real-observations', None, 'final-only-unverified', True),
        ('injected-invocation-change', {**meta, 'invocationId': inv}, 'worker-state-uncertain', False),
        ('injected-pid-change', {**meta, 'execMainPid': meta['execMainPid'] + 1}, 'worker-state-uncertain', False),
        ('injected-unit-change', {**meta, 'unit': 'unexpected-unit'}, 'worker-state-uncertain', False),
        ('injected-unit-disappearance', {**meta, 'assessment': 'rejected', 'systemdExecutionMetadataConsistent': False}, 'worker-state-uncertain', False),
        ('injected-second-missing', None, 'worker-state-uncertain', False),
        ('injected-second-unsafe', {**meta, 'executionAuthorized': True}, 'worker-state-uncertain', False),
    ]
    results = []
    real_calls = 0
    for name, injected, expected, consistent in cases:
        calls = []
        def probe(t):
            nonlocal real_calls
            calls.append(t)
            if len(calls) == 1 or name == 'stable-real-observations':
                real_calls += 1
                return historical.probe(t)
            return injected
        output = adapter.inspect(root, token, metadata_probe=probe)
        ok = (len(calls) == 2 and safe(output) and output.get('assessment') == expected
              and output.get('systemdExecutionMetadataConsistent') is consistent
              and output.get('receiptMetadataConsistent') is False)
        results.append({'scenario': name, 'passed': bool(ok), 'probeCalls': len(calls)})
    return results, real_calls


def run():
    runtime = load('display-worker-historical-runtime-v057994.py')
    previous = runtime.scenarios
    extra = []
    real_calls = [0]
    def wrapped(token, meta, receipt, *, actual_root=None):
        original = previous(token, meta, receipt, actual_root=actual_root)
        if actual_root is None:
            raise RuntimeError('Real isolated worker root required')
        scenarios, count = adversarial(token, meta, actual_root)
        extra.extend(scenarios)
        real_calls[0] += count
        return original  # Preserve the existing runtime's 11-scenario contract.
    runtime.scenarios = wrapped
    prior = runtime.run()
    if prior.get('selftest') != 'passed' or len(prior.get('results', [])) != 11 or not safe(prior):
        raise RuntimeError('Previous real systemd integration failed')
    if len(extra) != 7 or not all(x['passed'] for x in extra):
        raise RuntimeError('Adversarial cases failed: ' + repr(extra))
    result = summary(extra, True, real_calls[0])
    result['priorRuntimeScenariosPassed'] = 11
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'run'))
    args = parser.parse_args()
    try:
        output = selftest() if args.command == 'selftest' else run()
    except Exception as exc:
        output = dict(version='0.5.79.9.9', selftest='failed', error=str(exc),
                      productionModified=False, automaticCleanup=False,
                      executionAuthorized=False, physicalExecutionEnabled=False,
                      physicalRollbackVerified=False, commandsExecuted=0)
    print(json.dumps(output, indent=2))
    return 0 if output.get('selftest') == 'passed' else 2


if __name__ == '__main__':
    sys.exit(main())
