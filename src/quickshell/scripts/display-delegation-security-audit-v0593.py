#!/usr/bin/env python3
"""v0.5.93 offline delegation security audit. No display operations."""
import argparse
import ast
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ADMISSION = HERE / 'display_runtime_admission_v0592.py'
GLOBAL = HERE / 'display-global-runtime-gate-v0591.py'
SHARED = HERE / 'display-shared-admission-regression-v0592.py'


def audit():
    results = []
    def check(name, condition, detail=''):
        results.append({'name': name, 'passed': bool(condition), 'detail': detail})
    try:
        source = ADMISSION.read_text()
        tree = ast.parse(source)
        names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        gsource = GLOBAL.read_text()
        gtree = ast.parse(gsource)
        gnames = {node.name for node in gtree.body if isinstance(node, ast.FunctionDef)}
        check('admission_source_regular', ADMISSION.is_file() and not ADMISSION.is_symlink())
        check('global_source_regular', GLOBAL.is_file() and not GLOBAL.is_symlink())
        check('shared_regression_available', SHARED.is_file() and not SHARED.is_symlink())
        check('admission_ast_valid', tree is not None)
        check('global_ast_valid', gtree is not None)
        check('admission_entrypoint_present', 'admission' in names)
        check('descriptor_validation_present', '_validate' in names)
        check('runtime_root_validation_present', '_root' in names)
        check('child_descriptor_api_present', 'child_fds' in names)
        check('global_lock_present', 'global_lock' in gnames)
        check('global_lock_uses_shared_admission', 'from display_runtime_admission_v0592 import admission' in gsource)
        check('global_child_fds_passed', 'pass_fds=child_fds()' in gsource)
        check('runtime_lock_nonblocking', 'fcntl.LOCK_EX | fcntl.LOCK_NB' in source)
        check('no_symlink_open', 'os.O_NOFOLLOW' in source)
        check('no_physical_display_calls_in_admission', all(x not in source for x in ('hyprctl ', 'wlr-randr ', 'xrandr ', 'nvidia-settings ')))
        # This is a known limitation, not a pass claiming authenticated delegation.
        check('unauthenticated_delegation_gap_detected', 'fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)' in source and 'inherited = prior is not None' in source,
              'Inherited descriptor is validated, but no authenticated delegation token is implemented.')
    except (OSError, SyntaxError, UnicodeError) as exc:
        return {'selftest': 'failed', 'error': type(exc).__name__, 'results': results}
    passed = len(results) == 16 and all(row['passed'] for row in results)
    return {'version': '0.5.93', 'mode': 'offline', 'selftest': 'passed' if passed else 'failed',
            'totalScenarios': len(results), 'results': results,
            'legacyDirectEntrypointUnified': False,
            'authenticatedDelegationImplemented': False,
            'universalRuntimeExclusionVerified': False,
            'systemdIntegration': False,
            'productionModified': False,
            'executionAuthorized': False,
            'physicalExecutionEnabled': False,
            'commandsExecuted': 0,
            'limitations': ['Static and structural checks only; no live runtime exclusion proof',
                            'No authenticated delegated ownership',
                            'No physical monitor changes or rollback verification']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['selftest'])
    parser.parse_args()
    result = audit()
    print(json.dumps(result, indent=2))
    return 0 if result['selftest'] == 'passed' else 2

if __name__ == '__main__':
    sys.exit(main())
