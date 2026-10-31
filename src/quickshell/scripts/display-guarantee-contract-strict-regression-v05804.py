#!/usr/bin/env python3
"""v0.5.80.4: strict offline guarantee-result validation regression."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-guarantee-contract-matrix-v05801.py'
SAFE = ('historicalIdentityVerified', 'workerIdentityVerified', 'operationCompletionVerified',
        'finalizationAuthorized', 'executionAuthorized', 'physicalExecutionEnabled',
        'physicalRollbackVerified')


def load():
    spec = importlib.util.spec_from_file_location('serpantinum_v05804_matrix', SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError('Auditor unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selftest():
    validator = load().validate_output
    base = dict(selftest='passed', commandsExecuted=0, systemdIntegration=False,
                productionModified=False, executionAuthorized=False,
                physicalExecutionEnabled=False, physicalRollbackVerified=False,
                results=[dict(passed=True)], totalScenarios=1)
    cases = [
        ('valid-complete', {}, True),
        ('valid-two-scenarios', {'results': [dict(passed=True), dict(passed=True)], 'totalScenarios': 2}, True),
        ('missing-results', {'results': None}, False),
        ('missing-totalScenarios', {'totalScenarios': None}, False),
        ('missing-both', {'results': None, 'totalScenarios': None}, False),
        ('empty-results', {'results': [], 'totalScenarios': 0}, False),
        ('wrong-count', {'totalScenarios': 2}, False),
        ('boolean-count', {'totalScenarios': True}, False),
        ('float-count', {'totalScenarios': 1.0}, False),
        ('negative-count', {'totalScenarios': -1}, False),
        ('string-passed', {'results': [dict(passed='true')]}, False),
        ('integer-passed', {'results': [dict(passed=1)]}, False),
        ('non-dict-row', {'results': [True]}, False),
        ('boolean-commands', {'commandsExecuted': False}, False),
        ('runtime-overclaim', {'systemdIntegration': True}, False),
        ('atomic-overclaim', {'atomicCrossStoreSnapshot': True}, False),
        ('missing-critical-flag', {'executionAuthorized': None}, False),
        ('failed-selftest', {'selftest': 'failed'}, False),
        ('failed-scenario', {'results': [dict(passed=False)]}, False),
    ]
    results = []
    for name, mutations, expected in cases:
        candidate = dict(base)
        candidate.update(mutations)
        for key in ('results', 'totalScenarios'):
            if key in mutations and mutations[key] is None:
                candidate.pop(key)
        try:
            validator(name, candidate)
            accepted = True
        except RuntimeError:
            accepted = False
        assert accepted is expected, (name, accepted, expected)
        results.append(dict(scenario=name, passed=True, accepted=accepted))
    return dict(version='0.5.80.4', mode='offline-strict-guarantee-contract',
                selftest='passed', totalScenarios=len(results), results=results,
                hardeningImplemented=True, productionModified=False,
                systemdIntegration=False, automaticCleanup=False,
                atomicCrossStoreSnapshot=False, crashDurabilityVerified=False,
                **{key: False for key in SAFE}, commandsExecuted=0,
                limitations=['Offline validator checks only, not runtime proof',
                             'No independent worker authentication, power-loss or physical rollback proof'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
