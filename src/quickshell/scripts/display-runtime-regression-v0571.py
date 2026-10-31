#!/usr/bin/env python3
"""v0.5.71: opt-in Quickshell runtime regression; harmless process fixtures only."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile

FIXTURES = {
    'Concurrency.qml': ('8023716eecac186b93b970fc5a53f4c1da6144bb852aaa5d5f45dad8e2a04369',
                        {'starts': '2', 'exits': '2', 'stdout': '2', 'maxConcurrent': '1', 'passed': 'true'}),
    'Cancel.qml': ('b9e3e9c9919ad4650149fd3244164734fc0ac6a85299e5357c87b841e6df0441',
                   {'starts': '2', 'exits': '2', 'timeouts': '1', 'maxConcurrent': '1', 'blocked': 'false', 'retrySuccess': 'true', 'passed': 'true'}),
    'DoubleTimeout.qml': ('d3bca59910a4aa4bb114d7544d2931ba421267e0aeeb375b2d5a121b6122e36d',
                          {'starts': '2', 'exits': '2', 'timeouts': '2', 'blocked': 'true', 'maxConcurrent': '1', 'exitBeforeRetry': 'true', 'passed': 'true'}),
}
TIMEOUT = 8


def run_fixture(name, expected, source, work):
    target = work / name
    shutil.copyfile(source, target)
    cmd = ['quickshell', '-n', '-p', str(target)]
    env = os.environ.copy()
    env.update({'QS_CACHE_DIR': str(work / 'cache'), 'QS_STATE_DIR': str(work / 'state'),
                'QS_RUN_DIR': str(work / 'run'), 'QS_LOG_DIR': str(work / 'logs')})
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True, env=env, cwd=work)
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            output, _ = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate()
    lines = [re.sub(r'\x1b\[[0-9;]*m', '', line) for line in output.splitlines()]
    results = [line.split('V0571 RESULT ', 1)[1] for line in lines if 'V0571 RESULT ' in line]
    fields = dict(re.findall(r'([A-Za-z]+)=([^\s]+)', results[0])) if len(results) == 1 else {}
    valid = not timed_out and proc.returncode == 0 and len(results) == 1 and all(fields.get(k) == v for k, v in expected.items())
    return {'scenario': name, 'passed': valid, 'exitCode': proc.returncode,
            'timedOut': timed_out, 'result': fields,
            'error': '' if valid else '\n'.join(lines[-12:])[-1500:]}


def main():
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        print('Usage: python3 display-runtime-regression-v0571.py selftest', file=sys.stderr)
        return 2
    base = Path(__file__).resolve().parent / 'display-runtime-v0571'
    for name, (digest, _) in FIXTURES.items():
        src = base / name
        if not src.is_file() or src.is_symlink() or hashlib.sha256(src.read_bytes()).hexdigest() != digest:
            print(json.dumps({'version': '0.5.71', 'selftest': 'failed', 'error': 'Fixture missing, symlinked, or changed: ' + name}))
            return 2
    if shutil.which('quickshell') is None:
        print(json.dumps({'version': '0.5.71', 'selftest': 'failed', 'error': 'quickshell not found'}))
        return 2
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0571-runtime-') as tmp:
        os.chmod(tmp, 0o700)
        work = Path(tmp)
        for directory in ('cache', 'state', 'run', 'logs'):
            (work / directory).mkdir(mode=0o700)
        for name, (_, expected) in FIXTURES.items():
            try:
                results.append(run_fixture(name, expected, base / name, work))
            except (OSError, ValueError) as exc:
                results.append({'scenario': name, 'passed': False, 'error': str(exc)})
            if not results[-1]['passed']:
                break
    passed = len(results) == len(FIXTURES) and all(r['passed'] for r in results)
    print(json.dumps({'version': '0.5.71', 'mode': 'quickshell-runtime-process-fixtures',
                      'selftest': 'passed' if passed else 'failed', 'totalScenarios': len(results),
                      'results': results, 'physicalConfigurationCommandsExecuted': 0,
                      'note': 'Runtime fixture verification only; not production detector or physical rollback proof'}, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    sys.exit(main())
