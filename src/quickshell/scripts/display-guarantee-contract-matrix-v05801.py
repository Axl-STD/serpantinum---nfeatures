#!/usr/bin/env python3
"""v0.5.80.1: read-only guarantee contract inventory and offline regression gate.

This is an evidence matrix, NOT proof of physical safety, independent worker
identity, atomic cross-store snapshots, or power-loss durability.
"""
import argparse
import ast
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = {
    'atomic-publication': ('display-worker-receipt-atomic-regression-v05795.py', 'offline-behavior'),
    'process-crash-boundaries': ('display-worker-receipt-crash-boundaries-v05796.py', 'injected-process-exit'),
    'orphan-classification': ('display-worker-orphan-evidence-v05797.py', 'offline-behavior'),
    'orphan-negative-security': ('display-worker-orphan-security-regression-v057981.py', 'injected-filesystem-races'),
    'historical-state-binding': ('display-worker-historical-state-binding-v057991.py', 'injected-systemd-metadata'),
    'double-probe-runtime': ('display-worker-double-probe-runtime-v057998.py', 'offline-fixture-and-separate-runtime'),
    'adversarial-double-probe': ('display-worker-adversarial-double-probe-v057999.py', 'offline-fixture-and-separate-runtime'),
}
FALSE_FLAGS = ('historicalIdentityVerified', 'workerIdentityVerified',
               'operationCompletionVerified', 'finalizationAuthorized',
               'executionAuthorized', 'physicalExecutionEnabled',
               'physicalRollbackVerified', 'automaticCleanup',
               'atomicCrossStoreSnapshot', 'crashDurabilityVerified')
REQUIRED_FALSE = ('executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified')


def load(path):
    spec = importlib.util.spec_from_file_location('v05801_' + path.stem.replace('-', '_'), path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load ' + path.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_sources(root):
    results = []
    for label, (name, kind) in SOURCES.items():
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError('Missing or unsafe source: ' + name)
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        if 'selftest' not in functions:
            raise RuntimeError('Missing selftest: ' + name)
        results.append({'component': label, 'file': name, 'evidenceClass': kind,
                        'sourceParsed': True, 'selftestAvailable': True})
    return results


def validate_output(name, out):
    if not isinstance(out, dict) or out.get('selftest') != 'passed':
        raise RuntimeError(name + ': selftest failed')
    if type(out.get('commandsExecuted')) is not int or out['commandsExecuted'] != 0:
        raise RuntimeError(name + ': commandsExecuted contract failed')
    for key in REQUIRED_FALSE:
        if out.get(key) is not False:
            raise RuntimeError(name + ': unsafe or missing ' + key)
    for key in FALSE_FLAGS:
        if key in out and out[key] is not False:
            raise RuntimeError(name + ': permissive ' + key)
    if out.get('systemdIntegration') is not False:
        raise RuntimeError(name + ': offline selftest claimed systemd integration')
    if out.get('productionModified') is not False:
        raise RuntimeError(name + ': productionModified not false')
    if type(out.get('totalScenarios')) is not int or out['totalScenarios'] <= 0:
        raise RuntimeError(name + ': invalid or missing scenario count')
    rows = out.get('results')
    if not isinstance(rows, list) or len(rows) != out['totalScenarios']:
        raise RuntimeError(name + ': missing results or scenario count mismatch')
    if any(not isinstance(row, dict) or row.get('passed') is not True for row in rows):
        raise RuntimeError(name + ': failed or malformed scenario')
    return {'component': name, 'passed': True, 'scenarios': out.get('totalScenarios'),
            'mode': out.get('mode'), 'evidenceScope': 'offline-selftest-only'}


def negative_selftest():
    base = {'selftest': 'passed', 'commandsExecuted': 0, 'systemdIntegration': False,
            'productionModified': False, 'results': [{'passed': True}],
            'totalScenarios': 1, **{k: False for k in REQUIRED_FALSE}}
    cases = [('baseline', base, True),
             ('authorized', {**base, 'executionAuthorized': True}, False),
             ('physical', {**base, 'physicalExecutionEnabled': True}, False),
             ('rollback', {**base, 'physicalRollbackVerified': True}, False),
             ('missing-flag', {k: v for k, v in base.items() if k != 'executionAuthorized'}, False),
             ('wrong-count-type', {**base, 'commandsExecuted': False}, False),
             ('runtime-mislabel', {**base, 'systemdIntegration': True}, False),
             ('atomic-overclaim', {**base, 'atomicCrossStoreSnapshot': True}, False),
             ('cleanup-overclaim', {**base, 'automaticCleanup': True}, False),
             ('failed-scenario', {**base, 'results': [{'passed': False}], 'totalScenarios': 1}, False)]
    results = []
    for label, payload, should_accept in cases:
        try:
            validate_output(label, payload)
            accepted = True
        except RuntimeError:
            accepted = False
        if accepted != should_accept:
            raise AssertionError('Negative gate failed: ' + label)
        results.append({'scenario': label, 'passed': True})
    return results


def summary(mode, inventory, results):
    return {'version': '0.5.80.1', 'mode': mode, 'selftest': 'passed',
            'inventoryCount': len(inventory), 'inventory': inventory,
            'totalScenarios': len(results), 'results': results,
            'productionModified': False, 'systemdIntegration': False,
            'automaticCleanup': False, 'atomicCrossStoreSnapshot': False,
            'crashDurabilityVerified': False,
            **{k: False for k in FALSE_FLAGS}, 'commandsExecuted': 0,
            'limitations': ['Offline inventory and contract checks are not runtime or physical proof',
                            'Historical characterization is not treated as current production guarantees',
                            'No independent authentication, power-loss durability or physical rollback proof']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'verify'))
    args = parser.parse_args()
    inventory = inspect_sources(HERE)
    results = negative_selftest()
    if args.command == 'verify':
        for label, (name, _) in SOURCES.items():
            out = load(HERE / name).selftest()
            results.append(validate_output(label, out))
    print(json.dumps(summary('offline-contract-regression-gate' if args.command == 'verify'
                             else 'offline-source-contract-inventory', inventory, results),
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
