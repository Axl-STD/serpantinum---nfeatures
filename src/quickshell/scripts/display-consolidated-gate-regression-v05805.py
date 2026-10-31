#!/usr/bin/env python3
"""Offline negative tests for the v0.5.80.5 coordinator; no systemd execution."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location('v05805_gate', HERE / 'display-consolidated-regression-gate-v05805.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def selftest():
    mod = load()
    baseline = {'selftest': 'passed', 'totalScenarios': 1, 'results': [{'passed': True}],
                'commandsExecuted': 0, 'systemdIntegration': False,
                'productionModified': False, 'automaticCleanup': False,
                'executionAuthorized': False, 'physicalExecutionEnabled': False,
                'physicalRollbackVerified': False}
    tests = [('valid', baseline, True),
             ('missing-results', {k:v for k,v in baseline.items() if k != 'results'}, False),
             ('missing-count', {k:v for k,v in baseline.items() if k != 'totalScenarios'}, False),
             ('boolean-count', {**baseline, 'totalScenarios': True}, False),
             ('false-positive', {**baseline, 'results': [{'passed': 1}]}, False),
             ('unsafe-authorization', {**baseline, 'executionAuthorized': True}, False),
             ('runtime-mislabel', {**baseline, 'systemdIntegration': True}, False),
             ('nonzero-commands', {**baseline, 'commandsExecuted': 1}, False)]
    results = []
    for name, data, expected in tests:
        try:
            mod.validate(name, data, 1, False)
            accepted = True
        except ValueError:
            accepted = False
        assert accepted == expected, name
        results.append({'scenario': name, 'passed': True})
    return {'version': '0.5.80.5', 'mode': 'offline-coordinator-negative-regression',
            'selftest': 'passed', 'totalScenarios': len(results), 'results': results,
            'systemdIntegration': False, 'productionModified': False, 'automaticCleanup': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))
