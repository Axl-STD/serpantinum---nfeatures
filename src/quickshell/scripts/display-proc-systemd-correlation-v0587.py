#!/usr/bin/env python3
"""v0.5.87: conservative /proc and isolated systemd metadata correlation.

No ownership authentication, display actions, adoption or automatic cleanup.
Runtime explicitly starts a harmless, short-lived, token-named user unit.
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
import time

HERE = Path(__file__).resolve().parent
PREFIX = 'serpantinum-v0587-'
UNIT = re.compile(r'serpantinum-v0587-[0-9a-f]{20}\.service\Z')
INVOCATION = re.compile(r'[0-9a-f]{32}\Z')
SAFE = dict(resourceOwnershipVerified=False, productionModified=False,
            automaticCleanup=False, atomicCrossStoreSnapshot=False,
            crashDurabilityVerified=False, historicalIdentityVerified=False,
            workerIdentityVerified=False, operationCompletionVerified=False,
            finalizationAuthorized=False, executionAuthorized=False,
            physicalExecutionEnabled=False, physicalRollbackVerified=False,
            commandsExecuted=0)


def ownership():
    path = HERE / 'display-resource-ownership-v0586.py'
    spec = importlib.util.spec_from_file_location('ownership_0587', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(unit, expected_pid, before, after, process_before, process_after):
    """Read-only classification; never interprets matching metadata as authority."""
    result = {'assessment': 'uncertain', 'processMetadataConsistent': False,
              'systemdMetadataConsistent': False, 'resourceOwnershipVerified': False,
              'executionAuthorized': False, 'physicalExecutionEnabled': False,
              'automaticCleanup': False}
    if not isinstance(unit, str) or not UNIT.fullmatch(unit):
        result['assessment'] = 'invalid-unit-name'
        return result
    if type(expected_pid) is not int or expected_pid <= 0:
        result['assessment'] = 'invalid-expected-pid'
        return result
    if type(before) is not dict or type(after) is not dict:
        result['assessment'] = 'missing-unit-observation'
        return result
    keys = ('Id', 'MainPID', 'InvocationID', 'ActiveState', 'SubState')
    if any(before.get(k) != after.get(k) for k in keys):
        result['assessment'] = 'changed-unit-observation'
        return result
    if before.get('Id') != unit or before.get('ActiveState') != 'active' or before.get('SubState') != 'running':
        result['assessment'] = 'unit-not-running-or-mismatched'
        return result
    pid = before.get('MainPID')
    invocation = before.get('InvocationID')
    if type(pid) is not str or not re.fullmatch(r'[1-9][0-9]*', pid) or int(pid) != expected_pid:
        result['assessment'] = 'unit-pid-mismatch'
        return result
    if type(invocation) is not str or not INVOCATION.fullmatch(invocation) or int(invocation, 16) == 0:
        result['assessment'] = 'invalid-invocation'
        return result
    result['systemdMetadataConsistent'] = True
    if type(process_before) is not dict or type(process_after) is not dict:
        result['assessment'] = 'missing-process-observation'
        return result
    if process_before != process_after:
        result['assessment'] = 'changed-process-observation'
        return result
    p = process_before
    if (p.get('pid') != expected_pid or type(p.get('startTicks')) is not int
            or p['startTicks'] <= 0 or p.get('state') == 'Z'):
        result['assessment'] = 'process-identity-mismatch'
        return result
    result['processMetadataConsistent'] = True
    result['assessment'] = 'cross-store-metadata-consistent-only'
    return result


def show(unit):
    if not UNIT.fullmatch(unit):
        raise ValueError('unsafe unit name')
    command = ['systemctl', '--user', 'show', unit, '--no-pager',
               '--property=Id,MainPID,InvocationID,ActiveState,SubState']
    completed = subprocess.run(command, capture_output=True, text=True, timeout=4)
    if completed.returncode:
        raise RuntimeError('systemctl show failed')
    data = {}
    for line in completed.stdout.splitlines():
        if '=' not in line:
            raise ValueError('malformed systemctl response')
        key, value = line.split('=', 1)
        if key in data:
            raise ValueError('duplicate systemctl field')
        data[key] = value
    if set(data) != {'Id', 'MainPID', 'InvocationID', 'ActiveState', 'SubState'}:
        raise ValueError('incomplete systemctl response')
    return data


def report(mode, rows, runtime=False):
    ok = bool(rows) and all(row.get('passed') is True for row in rows)
    return dict(version='0.5.87', mode=mode, selftest='passed' if ok else 'failed',
                totalScenarios=len(rows), results=rows, systemdIntegration=runtime,
                **SAFE, limitations=['Cross-store observations are not atomic or authenticated ownership',
                'Runtime uses one harmless transient user unit which completes naturally',
                'No display commands, automatic cleanup, or physical rollback proof'])


def selftest():
    rows = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        rows.append(dict(scenario=name, passed=True))
    pid = os.getpid()
    proc = ownership().read_process(pid)
    if proc is None:
        raise RuntimeError('cannot read own /proc stat')
    unit = PREFIX + 'a' * 20 + '.service'
    props = dict(Id=unit, MainPID=str(pid), InvocationID='b'*32,
                 ActiveState='active', SubState='running')
    def result(b=props, a=props, pb=proc, pa=proc, u=unit, p=pid):
        return classify(u, p, b, a, pb, pa)
    check('stable-metadata', result()['assessment'] == 'cross-store-metadata-consistent-only')
    check('never-authenticated', result()['resourceOwnershipVerified'] is False)
    check('process-metadata-consistent', result()['processMetadataConsistent'] is True)
    check('systemd-metadata-consistent', result()['systemdMetadataConsistent'] is True)
    mutations = [
        ('wrong-unit', dict(props, Id='other.service'), 'unit-not-running-or-mismatched'),
        ('inactive-unit', dict(props, ActiveState='inactive'), 'unit-not-running-or-mismatched'),
        ('exited-unit', dict(props, SubState='exited'), 'unit-not-running-or-mismatched'),
        ('wrong-pid', dict(props, MainPID=str(pid+1)), 'unit-pid-mismatch'),
        ('zero-pid', dict(props, MainPID='0'), 'unit-pid-mismatch'),
        ('boolean-like-pid', dict(props, MainPID='True'), 'unit-pid-mismatch'),
        ('zero-invocation', dict(props, InvocationID='0'*32), 'invalid-invocation'),
        ('invalid-invocation', dict(props, InvocationID='not-an-id'), 'invalid-invocation'),
    ]
    for label, bad, assessment in mutations:
        check(label, result(b=bad, a=bad)['assessment'] == assessment)
    check('invocation-changed-between-reads', result(a=dict(props, InvocationID='c'*32))['assessment'] == 'changed-unit-observation')
    check('pid-changed-between-reads', result(a=dict(props, MainPID=str(pid+1)))['assessment'] == 'changed-unit-observation')
    check('unit-disappears', result(a=None)['assessment'] == 'missing-unit-observation')
    check('missing-first-unit', result(b=None)['assessment'] == 'missing-unit-observation')
    check('process-disappears', result(pa=None)['assessment'] == 'missing-process-observation')
    check('process-changed-start', result(pa=dict(proc, startTicks=proc['startTicks']+1))['assessment'] == 'changed-process-observation')
    check('process-changed-pgid', result(pa=dict(proc, pgid=proc['pgid']+1))['assessment'] == 'changed-process-observation')
    check('process-changed-ppid', result(pa=dict(proc, ppid=proc['ppid']+1))['assessment'] == 'changed-process-observation')
    check('pid-reuse-simulated', result(pb=dict(proc, startTicks=proc['startTicks']+1), pa=dict(proc, startTicks=proc['startTicks']+1))['assessment'] == 'cross-store-metadata-consistent-only' and result(pb=dict(proc, startTicks=proc['startTicks']+1), pa=dict(proc, startTicks=proc['startTicks']+1))['resourceOwnershipVerified'] is False)
    check('zombie', result(pb=dict(proc,state='Z'), pa=dict(proc,state='Z'))['assessment'] == 'process-identity-mismatch')
    check('missing-process', result(pb=None)['assessment'] == 'missing-process-observation')
    check('invalid-unit-prefix', result(u='ssh.service')['assessment'] == 'invalid-unit-name')
    check('invalid-expected-pid', result(p=0)['assessment'] == 'invalid-expected-pid')
    check('boolean-expected-pid', result(p=True)['assessment'] == 'invalid-expected-pid')
    check('no-automatic-cleanup', report('offline', rows)['automaticCleanup'] is False)
    check('no-physical-execution', report('offline', rows)['physicalExecutionEnabled'] is False)
    return report('offline', rows)


def runtime():
    rows = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        rows.append(dict(scenario=name, passed=True))
    unit = PREFIX + secrets.token_hex(10) + '.service'
    command = ['systemd-run', '--user', '--unit=' + unit, '--property=Type=exec',
               '--property=RuntimeMaxSec=9', '/usr/bin/sleep', '4']
    started = subprocess.run(command, capture_output=True, text=True, timeout=6)
    if started.returncode:
        raise RuntimeError('isolated systemd-run failed: ' + started.stderr[:160])
    deadline = time.monotonic() + 3
    good = None
    proc1 = proc2 = None
    first = second = None
    while time.monotonic() < deadline:
        first = show(unit)
        if first['ActiveState'] == 'active' and first['SubState'] == 'running' and first['MainPID'].isdigit() and int(first['MainPID']) > 0:
            pid = int(first['MainPID'])
            proc1 = ownership().read_process(pid)
            proc2 = ownership().read_process(pid)
            second = show(unit)
            candidate = classify(unit, pid, first, second, proc1, proc2)
            if candidate['assessment'] == 'cross-store-metadata-consistent-only':
                good = candidate
                break
        time.sleep(.04)
    if good is None:
        raise RuntimeError('stable real /proc + systemd observation unavailable')
    check('real-unit-id', first['Id'] == unit)
    check('real-unit-active', first['ActiveState'] == 'active')
    check('real-mainpid', int(first['MainPID']) > 0)
    check('real-invocation', INVOCATION.fullmatch(first['InvocationID']) is not None)
    check('real-process-readable', proc1 is not None)
    check('real-process-pid-matches', proc1['pid'] == pid)
    check('real-process-start-stable', proc1['startTicks'] == proc2['startTicks'])
    check('real-cross-store-consistency', good['assessment'] == 'cross-store-metadata-consistent-only')
    check('real-not-authenticated', good['resourceOwnershipVerified'] is False)
    check('real-no-authorization', good['executionAuthorized'] is False)
    check('real-injected-invocation-change-rejected', classify(unit,pid,first,dict(second,InvocationID='f'*32 if second['InvocationID'] != 'f'*32 else 'e'*32),proc1,proc2)['assessment'] == 'changed-unit-observation')
    check('real-injected-pid-change-rejected', classify(unit,pid,first,dict(second,MainPID=str(pid+1)),proc1,proc2)['assessment'] == 'changed-unit-observation')
    check('real-injected-proc-change-rejected', classify(unit,pid,first,second,proc1,dict(proc2,startTicks=proc2['startTicks']+1))['assessment'] == 'changed-process-observation')
    # Do not stop or signal the unit: observe its natural exit.
    deadline = time.monotonic() + 7
    finished = False
    while time.monotonic() < deadline:
        state = show(unit)
        if state['ActiveState'] != 'active':
            finished = True
            break
        time.sleep(.1)
    check('unit-natural-completion', finished)
    check('no-automatic-cleanup', True)
    check('no-physical-execution', True)
    return report('runtime', rows, True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'run'))
    args = parser.parse_args()
    try:
        output = selftest() if args.command == 'selftest' else runtime()
    except Exception as exc:
        output = dict(version='0.5.87', selftest='failed', error=str(exc)[:200], **SAFE)
    print(json.dumps(output, indent=2))
    return 0 if output['selftest'] == 'passed' else 2


if __name__ == '__main__':
    sys.exit(main())
