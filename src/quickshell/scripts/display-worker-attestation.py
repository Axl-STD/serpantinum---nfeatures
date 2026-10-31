#!/usr/bin/env python3
"""v0.5.45: read-only retained test-unit evidence; never authorizes finalization.

Only accepts the v0.5.36.1 isolated test unit contract. A matching retained
oneshot is not proof of historical process identity or physical rollback.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
FIELDS = ('Id', 'Description', 'LoadState', 'ActiveState', 'SubState',
          'MainPID', 'ControlPID', 'Result', 'Type', 'RemainAfterExit')


def verifier():
    path = HERE / 'display-systemd-reconciliation.py'
    spec = importlib.util.spec_from_file_location('serpantinum_reconcile_0545', path)
    if spec is None or spec.loader is None:
        raise ValueError('Verifier unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assess(token, props, query_error=None):
    module = verifier()
    name = module.unit_name(token)
    result = {'version': '0.5.45', 'mode': 'read-only-retained-test-unit-probe',
              'transactionId': token, 'unit': name, 'phase': 'unverified',
              'retainedTestUnitVerified': False, 'workerIdentityVerified': False,
              'workerInactivityVerified': False, 'historicalIdentityVerified': False,
              'operationCompletionVerified': False, 'finalizationAuthorized': False,
              'executionAuthorized': False, 'physicalExecutionEnabled': False,
              'physicalRollbackVerified': False, 'commandsExecuted': 0}
    if query_error is not None:
        result['phase'] = 'query-failed'
        result['reason'] = str(query_error)[:240]
        return result
    if not isinstance(props, dict) or any(k not in props for k in FIELDS):
        result['phase'] = 'incomplete-response'
        return result
    if props['LoadState'] == 'not-found':
        result['phase'] = 'unit-unloaded-unverifiable'
        return result
    try:
        module.interpret_unit(token, props)
    except ValueError as exc:
        result['phase'] = 'rejected'
        result['reason'] = str(exc)[:240]
        return result
    result['phase'] = 'retained-oneshot-exited'
    result['retainedTestUnitVerified'] = True
    result['workerInactivityVerified'] = True
    result['note'] = ('Current retained test-unit state only. Not proof of historical '
                      'worker identity, operation completion, or physical rollback.')
    return result


def probe(token, runner=subprocess.run):
    name = verifier().unit_name(token)
    try:
        proc = runner(['systemctl', '--user', 'show', name, '--no-pager',
                       *['-p' + k for k in FIELDS]],
                      capture_output=True, text=True, timeout=6)
        if proc.returncode:
            return assess(token, None, 'systemctl returned nonzero')
        data = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
        return assess(token, data)
    except (OSError, subprocess.SubprocessError) as exc:
        return assess(token, None, type(exc).__name__)


def selftest():
    m = verifier()
    token = 'a' * 24
    valid = {'Id': m.unit_name(token), 'Description': m.MARKER + token,
             'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited',
             'MainPID': '0', 'ControlPID': '0', 'Result': 'success',
             'Type': 'oneshot', 'RemainAfterExit': 'yes'}
    assert assess(token, valid)['retainedTestUnitVerified']
    cases = []
    for key, value in [('Id', m.unit_name('b'*24)), ('Description', 'wrong'),
                       ('LoadState', 'not-found'), ('ActiveState', 'inactive'),
                       ('SubState', 'running'), ('MainPID', '23'),
                       ('ControlPID', '24'), ('Result', 'exit-code'),
                       ('Type', 'simple'), ('RemainAfterExit', 'no')]:
        out = assess(token, {**valid, key: value})
        assert not out['retainedTestUnitVerified'] and not out['finalizationAuthorized']
        cases.append(key + '-rejected')
    assert assess(token, {}, None)['phase'] == 'incomplete-response'
    assert assess(token, None, 'timeout')['phase'] == 'query-failed'
    assert not assess(token, valid)['historicalIdentityVerified']
    assert not assess(token, valid)['operationCompletionVerified']
    return {'version': '0.5.45', 'selftest': 'passed', 'scenarios': cases +
            ['incomplete-response', 'query-failed', 'historical-identity-not-proven'],
            'physicalExecutionEnabled': False, 'finalizationAuthorized': False,
            'commandsExecuted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'probe'))
    parser.add_argument('--token')
    args = parser.parse_args()
    try:
        if args.command == 'selftest':
            result = selftest()
        else:
            if args.token is None:
                raise ValueError('Explicit 24-character token required')
            result = probe(args.token)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get('selftest') == 'passed' or result.get('retainedTestUnitVerified') else 2
    except (ValueError, OSError, TypeError, AssertionError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False,
                          'finalizationAuthorized': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
