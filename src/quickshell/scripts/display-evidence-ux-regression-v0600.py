#!/usr/bin/env python3
"""Regression for absent vs partial/unsafe diagnostic stores (read-only)."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

path = Path(__file__).with_name('display-coordination-contract.py')
spec = importlib.util.spec_from_file_location('evidence_contract', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
results = []

def check(name, roots, assessment, error=None):
    result = module.observe_stores_twice(*roots)
    assert result['assessment'] == assessment, (name, result)
    if error is not None:
        assert result.get('observationError') == error, (name, result)
    for key in ('executionAuthorized', 'finalizationAuthorized', 'reservationReusable',
                'physicalExecutionEnabled', 'physicalRollbackVerified',
                'atomicCrossStoreSnapshot', 'evidenceAuthenticated'):
        assert result[key] is False, (name, key)
    assert result['commandsExecuted'] == 0
    results.append({'scenario': name, 'passed': True})

with tempfile.TemporaryDirectory(prefix='serpantinum-evidence-ux-') as td:
    root = Path(td)
    root.chmod(0o700)
    dirs = tuple(root / n for n in ('bundle', 'journal', 'reservation'))
    check('all_absent', dirs, 'diagnostic-unavailable', 'all-stores-absent')
    for i, label in enumerate(('bundle', 'journal', 'reservation')):
        dirs[i].mkdir(mode=0o700)
        check('partial_' + label, dirs, 'observation-failed', 'read-or-validation-failed')
        dirs[i].rmdir()
    for i, label in enumerate(('bundle', 'journal', 'reservation')):
        dirs[i].symlink_to(root / 'missing')
        check('symlink_' + label, dirs, 'observation-failed', 'read-or-validation-failed')
        dirs[i].unlink()
    for d in dirs:
        d.mkdir(mode=0o700)
    check('all_empty_dirs', dirs, 'diagnostic-unavailable', 'required-journal-absent')
    journal = dirs[1] / module.JOURNAL
    journal.write_text('{bad json')
    journal.chmod(0o600)
    check('malformed_journal', dirs, 'observation-failed', 'read-or-validation-failed')
    journal.unlink()
    journal.symlink_to(root / 'missing')
    check('symlink_journal', dirs, 'observation-failed', 'read-or-validation-failed')
    journal.unlink()
    dirs[0].chmod(0o755)
    check('unsafe_directory', dirs, 'observation-failed', 'read-or-validation-failed')
    dirs[0].chmod(0o700)

legacy = module.selftest_observation()
assert legacy['selftest'] == 'passed'
print(json.dumps({'selftest': 'passed', 'totalScenarios': len(results),
                  'results': results, 'legacyScenarios': len(legacy['scenarios']),
                  'physicalExecutionEnabled': False, 'commandsExecuted': 0}, indent=2))
