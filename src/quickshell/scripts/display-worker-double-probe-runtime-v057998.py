#!/usr/bin/env python3
"""v0.5.79.9.8: isolated systemd double-probe runtime verification."""
import argparse
import json
import sys
from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent
SAFE = ('historicalIdentityVerified', 'workerIdentityVerified', 'operationCompletionVerified',
        'finalizationAuthorized', 'executionAuthorized', 'physicalExecutionEnabled',
        'physicalRollbackVerified')


def load(name):
    path = HERE / name
    spec = importlib.util.spec_from_file_location('v057998_' + name.replace('-', '_').replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe(out):
    return isinstance(out, dict) and out.get('commandsExecuted') == 0 and all(out.get(k) is False for k in SAFE)


def selftest():
    hardening = load('display-worker-cross-store-hardening-v057997.py')
    prior = hardening.selftest()
    assert prior['selftest'] == 'passed' and len(prior['results']) == 8
    assert all(x['passed'] for x in prior['results']) and safe(prior)
    results = [dict(scenario='offline-' + x['scenario'], passed=True) for x in prior['results']]
    return summary(results, False, 0)


def run():
    runtime = load('display-worker-historical-runtime-v057994.py')
    original_load = runtime.load
    counts = []

    def instrumented_load(name):
        module = original_load(name)
        if name != 'display-worker-historical-state-binding-v057991.py':
            return module
        original_inspect = module.inspect
        original_adapter_load = module.load

        def inspected(root, token, metadata=None, *, metadata_probe=None):
            calls = []
            def counted_probe(t):
                calls.append(t)
                if metadata_probe is not None:
                    return metadata_probe(t)
                historical = original_adapter_load('display-historical-worker-attestation-v05789.py', 'historical_v057998_probe')
                return historical.probe(t)
            # Do not replace or fake systemd data: count calls to the real probe.
            output = original_inspect(root, token, metadata, metadata_probe=counted_probe if metadata is None else None)
            counts.append((len(calls), output.get('assessment'), output.get('systemdExecutionMetadataConsistent')))
            return output
        module.inspect = inspected
        return module

    runtime.load = instrumented_load
    prior = runtime.run()
    if prior.get('selftest') != 'passed' or len(prior.get('results', [])) != 11 or not safe(prior):
        raise RuntimeError('Prior real systemd integration failed')
    if not counts or not any(n == 2 and consistent is True for n, _, consistent in counts) or not all(n in (1, 2) for n, _, _ in counts):
        raise RuntimeError('Expected two probes on consistent inspections and at most one on rejected initial metadata: ' + repr(counts))
    results = [dict(scenario=x['scenario'], passed=True) for x in prior['results']]
    results.append(dict(scenario='two-real-probes-per-inspection', passed=True, inspections=len(counts), probes=sum(n for n, _, _ in counts)))
    return summary(results, True, sum(n for n, _, _ in counts))


def summary(results, runtime, probes):
    return dict(version='0.5.79.9.8', mode='isolated-real-double-probe-runtime' if runtime else 'offline-double-probe-compatibility',
                selftest='passed' if all(x['passed'] for x in results) and len(results) == (12 if runtime else 8) else 'failed',
                totalScenarios=len(results), results=results, historicalProbeCalls=probes,
                systemdIntegration=runtime, productionModified=False, automaticCleanup=False,
                hardeningImplemented=True, atomicCrossStoreSnapshot=False,
                receiptMetadataConsistent=False, **{k: False for k in SAFE}, commandsExecuted=0,
                limitations=['Real unit is transient and innocuous; no display configuration commands',
                             'Stable repeated observations do not prove atomic cross-store snapshots',
                             'No independent authentication, power-loss durability or physical rollback proof'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'run'))
    args = parser.parse_args()
    try:
        output = selftest() if args.command == 'selftest' else run()
    except Exception as exc:
        output = dict(version='0.5.79.9.8', selftest='failed', error=str(exc),
                      executionAuthorized=False, physicalExecutionEnabled=False,
                      physicalRollbackVerified=False, commandsExecuted=0)
    print(json.dumps(output, indent=2))
    return 0 if output.get('selftest') == 'passed' else 2

if __name__ == '__main__':
    sys.exit(main())
