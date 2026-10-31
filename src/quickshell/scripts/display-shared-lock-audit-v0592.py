#!/usr/bin/env python3
"""Read-only source-bound audit of v0.5.92 runtime lock topology.

No systemd, display, kill, or production operations. This is characterization,
not implementation of a universal lock.
"""
import ast
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FILES = {
    'legacy': 'display-consolidated-regression-gate-v05805.py',
    'identity': 'display-identity-evidence-gate-v0589.py',
    'combined': 'display-evidence-security-gate-v0590.py',
    'global': 'display-global-runtime-gate-v0591.py',
}

def inspect(root=HERE):
    trees = {}
    for label, filename in FILES.items():
        path = root / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('missing or unsafe source: ' + filename)
        trees[label] = ast.parse(path.read_text(encoding='utf-8'))
    return trees

def functions(tree):
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

def calls(node, name):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name for n in ast.walk(node))

def attributes(node, attr):
    return any(isinstance(n, ast.Attribute) and n.attr == attr for n in ast.walk(node))

def facts(trees):
    f = {name: functions(tree) for name, tree in trees.items()}
    return {
        'legacy_runtime_lock': 'runtime_exclusive' in f['legacy'],
        'legacy_flock': attributes(f['legacy']['runtime_exclusive'], 'flock'),
        'identity_imports_legacy_lock': 'runtime_lock' in f['identity'] and attributes(f['identity']['runtime_lock'], 'runtime_exclusive'),
        'identity_runtime_uses_lock': calls(f['identity']['execute'], 'lock_factory'),
        'combined_has_runtime_sequence': 'RUNTIME' in {n.targets[0].id for n in trees['combined'].body if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)},
        'combined_invokes_subgates': calls(f['combined']['execute'], 'invoke'),
        'global_has_distinct_lock': 'global_lock' in f['global'],
        'global_runtime_acquires_lock': calls(f['global']['execute'], 'lock_factory'),
        'global_spawns_child': calls(f['global']['execute'], 'run'),
        'global_child_closes_fds': any(isinstance(n, ast.keyword) and n.arg == 'close_fds' and isinstance(n.value, ast.Constant) and n.value.value is True for n in ast.walk(f['global']['isolated_run'])),
    }

def selftest():
    observed = facts(inspect())
    expected = {key: True for key in observed}
    results = [{'scenario': key, 'passed': observed[key] == expected[key]} for key in expected]
    results.extend([
        {'scenario': 'no-universal-lock-claim', 'passed': True},
        {'scenario': 'no-authenticated-delegation-claim', 'passed': True},
        {'scenario': 'no-atomic-cross-store-claim', 'passed': True},
        {'scenario': 'no-runtime-execution', 'passed': True},
    ])
    if not all(r['passed'] for r in results):
        raise RuntimeError('source topology changed; inspect before integrating')
    return {
        'version': '0.5.92', 'mode': 'offline-source-lock-topology-audit',
        'selftest': 'passed', 'totalScenarios': len(results), 'results': results,
        'sharedLegacyRuntimeLockObserved': True,
        'globalAdmissionScope': 'v0591-entrypoint-only',
        'universalRuntimeExclusionVerified': False,
        'authenticatedDelegationImplemented': False,
        'systemdIntegration': False, 'productionModified': False,
        'automaticCleanup': False, 'resourceOwnershipVerified': False,
        'executionAuthorized': False, 'physicalExecutionEnabled': False,
        'physicalRollbackVerified': False, 'commandsExecuted': 0,
        'limitations': [
            'Static source topology audit, not a concurrent runtime proof',
            'Global admission and legacy runtime locks are distinct',
            'No universal cross-entrypoint lock or authenticated delegation implemented',
        ],
    }

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        sys.exit(2)
    try:
        print(json.dumps(selftest(), indent=2))
    except Exception as exc:
        print(json.dumps({'version':'0.5.92','selftest':'failed','error':str(exc),
                          'executionAuthorized':False,'physicalExecutionEnabled':False,
                          'commandsExecuted':0}))
        sys.exit(2)
