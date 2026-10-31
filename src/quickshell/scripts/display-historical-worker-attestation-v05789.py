#!/usr/bin/env python3
"""v0.5.78.9: read-only systemd execution metadata characterization.

Does not authorize execution, finalization, historical worker identity, or rollback.
The unit must conform to the existing v0.5.36.1 retained-test-unit contract.
"""
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
EXTRA = ('InvocationID', 'ExecMainPID', 'ExecMainCode', 'ExecMainStatus',
         'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'NRestarts')
HEX32 = re.compile(r'[0-9a-fA-F]{32}\Z')
POSINT = re.compile(r'[1-9][0-9]*\Z')


def verifier():
    path = HERE / 'display-systemd-reconciliation.py'
    spec = importlib.util.spec_from_file_location('serpantinum_v05789_reconcile', path)
    if spec is None or spec.loader is None:
        raise ValueError('Verifier unavailable')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assess(token, props, query_error=None):
    mod = verifier()
    unit = mod.unit_name(token)
    result = {'version': '0.5.78.9', 'mode': 'read-only-systemd-execution-metadata',
              'transactionId': token, 'unit': unit, 'assessment': 'unverified',
              'systemdExecutionMetadataConsistent': False,
              'historicalIdentityVerified': False, 'workerIdentityVerified': False,
              'operationCompletionVerified': False, 'finalizationAuthorized': False,
              'executionAuthorized': False, 'physicalExecutionEnabled': False,
              'physicalRollbackVerified': False, 'commandsExecuted': 0}
    if query_error is not None:
        result['assessment'] = 'query-failed'
        return result
    if not isinstance(props, dict) or any(k not in props for k in EXTRA):
        result['assessment'] = 'incomplete-response'
        return result
    try:
        mod.interpret_unit(token, props)
        invocation = props['InvocationID']
        if not isinstance(invocation, str) or not HEX32.fullmatch(invocation) or int(invocation, 16) == 0:
            raise ValueError('Invalid InvocationID')
        if not isinstance(props['ExecMainPID'], str) or not POSINT.fullmatch(props['ExecMainPID']):
            raise ValueError('Invalid historical PID')
        if props['ExecMainCode'] != '1' or props['ExecMainStatus'] != '0':
            raise ValueError('Unexpected process termination')
        if props['NRestarts'] != '0':
            raise ValueError('Unexpected restart count')
        def parse_timestamp(value):
            if not isinstance(value, str) or not value.strip() or value.strip() == 'n/a':
                raise ValueError('Missing timestamp')
            # systemd textual timestamps, as observed on Arch; locale-dependent strings

            match = re.fullmatch(r'[A-Za-z]{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) [A-Za-z0-9_+/-]+', value)
            if not match:
                raise ValueError('Invalid timestamp format')
            return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
        start = parse_timestamp(props['ExecMainStartTimestamp'])
        end = parse_timestamp(props['ExecMainExitTimestamp'])
        if end < start:
            raise ValueError('Reversed timestamps')
    except (ValueError, TypeError, KeyError) as exc:
        result['assessment'] = 'rejected'
        result['reason'] = str(exc)[:180]
        return result
    result['assessment'] = 'systemd-execution-metadata-consistent-only'
    result['systemdExecutionMetadataConsistent'] = True
    result['invocationId'] = invocation.lower()
    result['execMainPid'] = int(props['ExecMainPID'])
    result['note'] = 'Retained unit metadata only; no independent binding to worker action or physical rollback.'
    return result


def probe(token, runner=subprocess.run):
    mod = verifier()
    unit = mod.unit_name(token)
    fields = ('Id', 'Description', 'LoadState', 'ActiveState', 'SubState',
              'MainPID', 'ControlPID', 'Result', 'Type', 'RemainAfterExit') + EXTRA
    try:
        proc = runner(['systemctl', '--user', 'show', unit, '--no-pager',
                       *['-p' + k for k in fields]], capture_output=True, text=True, timeout=6)
        if proc.returncode:
            return assess(token, None, 'systemctl returned nonzero')
        data = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
        return assess(token, data)
    except (OSError, subprocess.SubprocessError):
        return assess(token, None, 'systemctl query failed')


def selftest():
    mod = verifier()
    token = 'a' * 24
    base = {'Id': mod.unit_name(token), 'Description': mod.MARKER + token,
            'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited',
            'MainPID': '0', 'ControlPID': '0', 'Result': 'success',
            'Type': 'oneshot', 'RemainAfterExit': 'yes',
            'InvocationID': '1' * 32, 'ExecMainPID': '12345',
            'ExecMainCode': '1', 'ExecMainStatus': '0',
            'ExecMainStartTimestamp': 'Sat 2026-10-31 12:58:10 CST',
            'ExecMainExitTimestamp': 'Sat 2026-10-31 12:58:10 CST',
            'NRestarts': '0'}
    cases = [('consistent-metadata', base, True)]
    for key, value in [('Id', mod.unit_name('b' * 24)),
                       ('Description', 'wrong'), ('LoadState', 'not-found'),
                       ('SubState', 'running'), ('Result', 'exit-code'),
                       ('MainPID', '3'), ('InvocationID', ''),
                       ('InvocationID', '0' * 32), ('InvocationID', 'xyz'),
                       ('ExecMainPID', '0'), ('ExecMainPID', '-1'),
                       ('ExecMainCode', '2'), ('ExecMainStatus', '1'),
                       ('NRestarts', '1'), ('ExecMainStartTimestamp', ''),
                       ('ExecMainExitTimestamp', 'Fri 2026-10-30 12:58:10 CST')]:
        cases.append((key + '-rejected', {**base, key: value}, False))
    cases.append(('missing-field-rejected', {k:v for k,v in base.items() if k != 'InvocationID'}, False))
    cases.append(('query-error-rejected', None, False))
    results = []
    for name, props, expected in cases:
        out = assess(token, props, 'injected error' if name == 'query-error-rejected' else None)
        assert out['systemdExecutionMetadataConsistent'] is expected, name
        for key in ('historicalIdentityVerified', 'workerIdentityVerified',
                    'operationCompletionVerified', 'finalizationAuthorized',
                    'executionAuthorized', 'physicalExecutionEnabled', 'physicalRollbackVerified'):
            assert out[key] is False, (name, key)
        assert out['commandsExecuted'] == 0, name
        results.append({'scenario': name, 'passed': True})
    return {'version': '0.5.78.9', 'mode': 'offline-source-bound-systemd-metadata',
            'selftest': 'passed', 'totalScenarios': len(results), 'results': results,
            'productionModified': False, 'systemdIntegration': False,
            'historicalIdentityVerified': False, 'executionAuthorized': False,
            'physicalExecutionEnabled': False, 'physicalRollbackVerified': False,
            'commandsExecuted': 0,
            'limitations': ['No systemd service launched in selftest',
                            'Timestamp parsing assumes English systemd textual format',
                            'Metadata consistency is not independent proof of worker identity or physical rollback']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('selftest', 'probe'))
    parser.add_argument('--token')
    args = parser.parse_args()
    if args.command == 'selftest':
        out = selftest()
    else:
        if args.token is None:
            raise ValueError('Explicit transaction token required')
        out = probe(args.token)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get('selftest') == 'passed' or out.get('systemdExecutionMetadataConsistent') else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, AssertionError, OSError, TypeError) as exc:
        print(json.dumps({'error': str(exc), 'executionAuthorized': False,
                          'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
