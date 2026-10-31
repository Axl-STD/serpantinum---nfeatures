#!/usr/bin/env python3
"""v0.5.55: allowlisted offline selftests and cross-store classification."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FILES = (
    'display-global-lock.py',
    'display-coordination-rehearsal.py',
    'display-coordination-finalization.py',
    'display-coordination-contract.py',
    'display-worker-attestation.py',
    'display-evidence-link-v0546.py',
    'display-evidence-consistency-v0547.py',
)
EXPECTED = (5, 6, 7, 10, 13, 5, 7)
# Imported for pure unit-property checks only; never executed as a CLI.
DEPENDENCIES = ('display-systemd-reconciliation.py',)
TIMEOUT = 20


def objects_from_stdout(output):
    decoder = json.JSONDecoder()
    pos = 0
    found = []
    while pos < len(output):
        while pos < len(output) and output[pos].isspace():
            pos += 1
        if pos == len(output):
            break
        obj, consumed = decoder.raw_decode(output, pos)
        if not isinstance(obj, dict):
            raise ValueError('Non-object JSON output')
        found.append(obj)
        pos = consumed
    if not found:
        raise ValueError('No JSON results')
    return found


def verify(name, objs, expected):
    for obj in objs:
        for key in ('physicalExecutionEnabled', 'executionAuthorized',
                    'physicalRollbackVerified', 'finalizationAuthorized'):
            if key in obj and obj[key] is not False:
                raise ValueError(name + ': unsafe flag ' + key)
        if 'commandsExecuted' in obj and (type(obj['commandsExecuted']) is not int or obj['commandsExecuted'] != 0):
            raise ValueError(name + ': commandsExecuted not zero')
    final = objs[-1]
    if final.get('selftest') != 'passed' and final.get('phase') != 'selftest-passed':
        raise ValueError(name + ': missing success marker')
    scenarios = final.get('scenarios')
    if not isinstance(scenarios, list) or len(scenarios) != expected or len(set(scenarios)) != expected:
        raise ValueError(name + ': scenario count/uniqueness mismatch')
    if 'commandsExecuted' not in final:
        raise ValueError(name + ': missing commandsExecuted')
    if name in ('display-evidence-link-v0546.py', 'display-evidence-consistency-v0547.py'):
        if final.get('executionAuthorized') is not False:
            raise ValueError(name + ': missing explicit denial of authorization')
    elif final.get('physicalExecutionEnabled') is not False:
        raise ValueError(name + ': missing explicit physical execution denial')
    return scenarios


def run_suite(source):
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0549-offline-') as tmp:
        os.chmod(tmp, 0o700)
        work = Path(tmp)
        for filename in FILES + DEPENDENCIES:
            path = source / filename
            if not path.is_file() or path.is_symlink():
                raise ValueError('Missing or symlinked allowlisted file: ' + filename)
            shutil.copyfile(path, work / filename)
        for filename, expected in zip(FILES, EXPECTED):
            try:
                proc = subprocess.run([sys.executable, '-I', str(work / filename), 'selftest'],
                                      cwd=work, capture_output=True, text=True, timeout=TIMEOUT,
                                      env={'PATH': '/usr/bin:/bin', 'HOME': str(work),
                                           'TMPDIR': str(work), 'PYTHONDONTWRITEBYTECODE': '1'})
                if proc.returncode != 0:
                    raise ValueError('Exit ' + str(proc.returncode) + ': ' + proc.stderr[-350:])
                scenarios = verify(filename, objects_from_stdout(proc.stdout), expected)
                results.append({'controller': filename, 'passed': True, 'scenarios': len(scenarios)})
            except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                results.append({'controller': filename, 'passed': False, 'error': str(exc)[:350]})
                break
        if len(results) == len(FILES) and all(r['passed'] for r in results):
            name = 'display-coordination-contract.py:stores'
            try:
                proc = subprocess.run([sys.executable, '-I', str(work / 'display-coordination-contract.py'), 'selftest-stores'],
                                      cwd=work, capture_output=True, text=True, timeout=TIMEOUT,
                                      env={'PATH': '/usr/bin:/bin', 'HOME': str(work),
                                           'TMPDIR': str(work), 'PYTHONDONTWRITEBYTECODE': '1'})
                if proc.returncode != 0:
                    raise ValueError('Exit ' + str(proc.returncode) + ': ' + proc.stderr[-350:])
                scenarios = verify(name, objects_from_stdout(proc.stdout), 8)
                results.append({'controller': name, 'passed': True, 'scenarios': len(scenarios)})
            except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                results.append({'controller': name, 'passed': False, 'error': str(exc)[:350]})
        if len(results) == len(FILES) + 1 and all(r['passed'] for r in results):
            name = 'display-coordination-contract.py:observation'
            try:
                proc = subprocess.run([sys.executable, '-I', str(work / 'display-coordination-contract.py'), 'selftest-observation'],
                                      cwd=work, capture_output=True, text=True, timeout=TIMEOUT,
                                      env={'PATH': '/usr/bin:/bin', 'HOME': str(work),
                                           'TMPDIR': str(work), 'PYTHONDONTWRITEBYTECODE': '1'})
                if proc.returncode != 0:
                    raise ValueError('Exit ' + str(proc.returncode) + ': ' + proc.stderr[-350:])
                scenarios = verify(name, objects_from_stdout(proc.stdout), 7)
                results.append({'controller': name, 'passed': True, 'scenarios': len(scenarios)})
            except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                results.append({'controller': name, 'passed': False, 'error': str(exc)[:350]})
        if len(results) == len(FILES) + 2 and all(r['passed'] for r in results):
            name = 'display-coordination-contract.py:secure-json'
            try:
                proc = subprocess.run([sys.executable, '-I', str(work / 'display-coordination-contract.py'), 'selftest-secure-json'],
                                      cwd=work, capture_output=True, text=True, timeout=TIMEOUT,
                                      env={'PATH': '/usr/bin:/bin', 'HOME': str(work),
                                           'TMPDIR': str(work), 'PYTHONDONTWRITEBYTECODE': '1'})
                if proc.returncode != 0:
                    raise ValueError('Exit ' + str(proc.returncode) + ': ' + proc.stderr[-350:])
                scenarios = verify(name, objects_from_stdout(proc.stdout), 7)
                results.append({'controller': name, 'passed': True, 'scenarios': len(scenarios)})
            except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                results.append({'controller': name, 'passed': False, 'error': str(exc)[:350]})
    success = len(results) == len(FILES) + 3 and all(r['passed'] for r in results)
    return {'version': '0.5.55', 'mode': 'allowlisted-offline-regression',
            'selftest': 'passed' if success else 'failed', 'results': results,
            'totalScenarios': sum(EXPECTED) + 22 if success else None,
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0,
            'systemdIntegration': False, 'liveBundleTouched': False,
            'note': 'Offline selftests only; no physical safety or rollback proof'}, success


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest',))
    args = parser.parse_args()
    try:
        result, success = run_suite(Path(__file__).resolve().parent)
    except (ValueError, OSError) as exc:
        result, success = {'version': '0.5.55', 'selftest': 'failed', 'error': str(exc),
                           'executionAuthorized': False}, False
    print(json.dumps(result, indent=2))
    return 0 if success else 2


if __name__ == '__main__':
    sys.exit(main())
