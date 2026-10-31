#!/usr/bin/env python3
"""Serpantinum v0.5.36.1: isolated systemd-backed reconciliation test.

Never observes or modifies live displays. Only injected monitor fixtures are used.
Never changes the recovery journal or authorizes physical rollback.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[0-9a-f]{24}\Z')
PREFIX = 'serpantinum-v0536-reconcile-'
MARKER = 'Serpantinum isolated reconciliation test '


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit_name(token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid transaction token')
    return PREFIX + token + '.service'


def interpret_unit(token, properties):
    """Fail closed for missing, active, ambiguous or unbound units."""
    expected = unit_name(token)
    if properties.get('Id') != expected:
        raise ValueError('Systemd unit identity mismatch')
    if properties.get('Description') != MARKER + token:
        raise ValueError('Systemd unit description mismatch')
    if properties.get('LoadState') != 'loaded':
        raise ValueError('Systemd unit not loaded')
    if properties.get('ActiveState') != 'active' or properties.get('SubState') != 'exited':
        raise ValueError('Retained oneshot is not confirmed exited')
    if properties.get('Type') != 'oneshot' or properties.get('RemainAfterExit') != 'yes':
        raise ValueError('Unexpected service execution contract')
    if properties.get('Result') != 'success':
        raise ValueError('Worker did not complete successfully')
    if properties.get('MainPID') != '0' or properties.get('ControlPID') != '0':
        raise ValueError('Worker PID still present')
    return True


def query_unit(token):
    name = unit_name(token)
    props = ('Id', 'Description', 'LoadState', 'ActiveState', 'SubState', 'MainPID',
             'ControlPID', 'Result', 'Type', 'RemainAfterExit')
    result = subprocess.run(['systemctl', '--user', 'show', name, '--no-pager',
                             *['-p' + item for item in props]],
                            capture_output=True, text=True, timeout=6)
    if result.returncode != 0:
        raise ValueError('Systemd query failed: ' + result.stderr[:180])
    lines = [line.split('=', 1) for line in result.stdout.splitlines() if '=' in line]
    data = dict(lines)
    if any(item not in data for item in props):
        raise ValueError('Incomplete systemd response')
    return interpret_unit(token, data)


def fixture():
    return [{'name': 'eDP-1', 'width': 1920, 'height': 1080, 'x': 0, 'y': 0,
             'scale': 1.0, 'transform': 0, 'disabled': False,
             'mirrorOf': 'none', 'refreshRate': 60.061}]


def setup(root, token):
    persistence = load('display-recovery-persistence.py', 'persistence_setup_0536')
    persistence.init(root, token, 15)
    for event, now in [('supervisor-ready', 1), ('reserve', 2),
                       ('initiator-exited', 3), ('expire', 16), ('recover', 17)]:
        persistence.advance(root, token, event, now)
    return persistence


def reconcile(root, token, scenario, unit_probe=query_unit):
    if scenario not in ('equivalent', 'different', 'observer-failure'):
        raise ValueError('Unknown test scenario')
    module = load('display-recovery-reconcile.py', 'reconcile_0536')
    expected = fixture()
    def observe():
        if scenario == 'observer-failure':
            raise RuntimeError('Injected observer failure')
        return [dict(expected[0], x=100)] if scenario == 'different' else [dict(expected[0])]
    return module.reconcile(root, token, expected, observe, lambda: unit_probe(token))


def selftest():
    token = 'a' * 24
    valid = {'Id': unit_name(token), 'Description': MARKER + token,
             'LoadState': 'loaded', 'ActiveState': 'active',
             'SubState': 'exited', 'MainPID': '0', 'ControlPID': '0',
             'Result': 'success', 'Type': 'oneshot', 'RemainAfterExit': 'yes'}
    assert interpret_unit(token, valid)
    rejected = 0
    for key, bad in [('Id', unit_name('b'*24)), ('Description', 'wrong'),
                     ('LoadState', 'not-found'), ('ActiveState', 'inactive'),
                     ('SubState', 'running'), ('MainPID', '42'), ('ControlPID', '42'),
                     ('Result', 'exit-code'), ('Type', 'simple'), ('RemainAfterExit', 'no')]:
        try:
            interpret_unit(token, dict(valid, **{key: bad}))
        except ValueError:
            rejected += 1
        else:
            raise AssertionError('Unsafe systemd response accepted: ' + key)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0536-selftest-') as root:
        os.chmod(root, 0o700)
        persistence = setup(root, token)
        try:
            reconcile(root, token, 'equivalent', unit_probe=lambda _: False)
        except ValueError:
            pass
        else:
            raise AssertionError('Active worker accepted')
        assert persistence.status(root)['state']['phase'] == 'rollback-running'
        result = reconcile(root, token, 'equivalent', unit_probe=lambda _: True)
        assert result['assessment'] == 'observable-equivalent'
        assert persistence.status(root)['state']['phase'] == 'rollback-running'
    return {'selftest': 'passed', 'invalidUnitStatesRejected': rejected,
            'activeWorkerRejected': True, 'journalUnchanged': True,
            'systemdIntegration': 'not-run', 'commandsExecuted': 0,
            'physicalRollbackVerified': False}


def stop_test_unit(token):
    """Stop only our retained test unit after verifying its unique identity."""
    name = unit_name(token)
    probe = subprocess.run(['systemctl', '--user', 'show', name, '--no-pager',
                            '-pId', '-pDescription', '-pLoadState'],
                           capture_output=True, text=True, timeout=6)
    if probe.returncode != 0:
        raise RuntimeError('Cannot verify unit before cleanup: ' + probe.stderr[:180])
    data = dict(line.split('=', 1) for line in probe.stdout.splitlines() if '=' in line)
    if data.get('LoadState') == 'not-found':
        return
    if data.get('Id') != name or data.get('Description') != MARKER + token:
        raise RuntimeError('Refusing cleanup of unrecognized unit')
    result = subprocess.run(['systemctl', '--user', 'stop', name],
                            capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError('Cannot stop isolated test unit: ' + result.stderr[:180])


def run():
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05361-', dir=os.environ.get('XDG_RUNTIME_DIR')) as base:
        os.chmod(base, 0o700)
        for scenario in ('equivalent', 'different', 'observer-failure'):
            token = secrets.token_hex(12)
            root = Path(base) / scenario
            root.mkdir(mode=0o700)
            persistence = setup(root, token)
            name = unit_name(token)
            launched = False
            try:
                launch = subprocess.run(['systemd-run', '--user', '--quiet', '--unit=' + name[:-8],
                                         '--property=Type=oneshot', '--property=RemainAfterExit=yes',
                                         '--description=' + MARKER + token, '/usr/bin/true'],
                                        capture_output=True, text=True, timeout=10)
                if launch.returncode:
                    raise RuntimeError('Could not create isolated unit: ' + launch.stderr[:200])
                launched = True
                deadline = time.monotonic() + 8
                last_error = 'not queried'
                while True:
                    try:
                        query_unit(token)
                        break
                    except ValueError as exc:
                        last_error = str(exc)
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Unit did not become verifiably exited: ' + last_error)
                        time.sleep(.1)
                before = persistence.status(root)
                result = reconcile(root, token, scenario)
                after = persistence.status(root)
                expected = {'equivalent': 'observable-equivalent', 'different': 'observable-different',
                            'observer-failure': 'observation-failed'}[scenario]
                if result['assessment'] != expected or before != after:
                    raise RuntimeError('Unexpected reconciliation result or journal changed')
                results.append({'scenario': scenario, 'assessment': result['assessment'],
                                'journalUnchanged': True, 'physicalRollbackVerified': False})
            finally:
                if launched:
                    stop_test_unit(token)
    return {'version': '0.5.36.1', 'mode': 'isolated-systemd-reconciliation',
            'scenarios': results, 'commandsExecuted': 0,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'liveBundleTouched': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'run'))
    args = parser.parse_args()
    result = selftest() if args.command == 'selftest' else run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
