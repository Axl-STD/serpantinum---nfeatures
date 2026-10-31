#!/usr/bin/env python3
"""v0.5.51: five allowlisted offline tests; systemd actions never invoked."""
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
)
EXPECTED = (5, 6, 7, 10, 13)
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
    for key in ('physicalExecutionEnabled', 'commandsExecuted'):
        if key not in final:
            raise ValueError(name + ': missing safety field ' + key)
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
    success = len(results) == len(FILES) and all(r['passed'] for r in results)
    return {'version': '0.5.51', 'mode': 'allowlisted-offline-regression',
            'selftest': 'passed' if success else 'failed', 'results': results,
            'totalScenarios': sum(EXPECTED) if success else None,
            'physicalExecutionEnabled': False, 'executionAuthorized': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0,
            'systemdIntegration': False, 'liveBundleTouched': False}, success


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest',))
    args = parser.parse_args()
    try:
        result, success = run_suite(Path(__file__).resolve().parent)
    except (ValueError, OSError) as exc:
        result, success = {'version': '0.5.51', 'selftest': 'failed', 'error': str(exc),
                           'executionAuthorized': False}, False
    print(json.dumps(result, indent=2))
    return 0 if success else 2


if __name__ == '__main__':
    sys.exit(main())
