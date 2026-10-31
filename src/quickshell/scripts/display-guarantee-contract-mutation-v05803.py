#!/usr/bin/env python3
"""v0.5.80.3: source-bound offline mutation characterization; no production changes."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'display-guarantee-contract-matrix-v05801.py'


def load():
    spec = importlib.util.spec_from_file_location('serpantinum_contract_v05801', SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError('Auditor source unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selftest():
    auditor = load()
    base = dict(selftest='passed', commandsExecuted=0, systemdIntegration=False,
                productionModified=False, executionAuthorized=False,
                physicalExecutionEnabled=False, physicalRollbackVerified=False,
                results=[dict(passed=True)], totalScenarios=1)
    cases = [
        ('valid-complete', {}, True, None),
        ('missing-results', {'results': None}, True, 'MISSING_RESULTS_ACCEPTED'),
        ('missing-totalScenarios', {'totalScenarios': None}, False, None),
        ('missing-both', {'results': None, 'totalScenarios': None}, True, 'MISSING_SCENARIO_EVIDENCE_ACCEPTED'),
        ('empty-results', {'results': [] , 'totalScenarios': 0}, False, None),
        ('wrong-count', {'totalScenarios': 2}, False, None),
        ('boolean-count', {'totalScenarios': True}, True, 'BOOLEAN_SCENARIO_COUNT_ACCEPTED'),
        ('string-passed', {'results': [dict(passed='true')]}, False, None),
        ('boolean-commands', {'commandsExecuted': False}, False, None),
        ('runtime-overclaim', {'systemdIntegration': True}, False, None),
        ('atomic-overclaim', {'atomicCrossStoreSnapshot': True}, False, None),
        ('missing-critical-flag', {'executionAuthorized': None}, False, None),
        ('failed-selftest', {'selftest': 'failed'}, False, None),
        ('failed-scenario', {'results': [dict(passed=False)]}, False, None),
    ]
    findings, results = [], []
    for name, changes, expected, finding in cases:
        candidate = dict(base)
        candidate.update(changes)
        for key in ('results', 'totalScenarios'):
            if key in changes and changes[key] is None:
                candidate.pop(key)
        try:
            auditor.validate_output(name, candidate)
            accepted = True
        except RuntimeError:
            accepted = False
        if accepted != expected:
            raise AssertionError(f'{name}: expected accepted={expected}, got {accepted}')
        row = dict(scenario=name, passed=True, accepted=accepted)
        if finding:
            findings.append(finding)
            row['finding'] = finding
        results.append(row)
    return dict(version='0.5.80.3', mode='source-bound-offline-contract-mutation-characterization',
                selftest='passed', totalScenarios=len(results), results=results,
                findings=findings, hardeningComplete=False, productionModified=False,
                systemdIntegration=False, automaticCleanup=False,
                atomicCrossStoreSnapshot=False, crashDurabilityVerified=False,
                historicalIdentityVerified=False, workerIdentityVerified=False,
                operationCompletionVerified=False, finalizationAuthorized=False,
                executionAuthorized=False, physicalExecutionEnabled=False,
                physicalRollbackVerified=False, commandsExecuted=0,
                limitations=['Characterizes existing validator, does not repair it',
                             'No dependent selftests or systemd runtime executed',
                             'No physical safety or independent identity proof'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
