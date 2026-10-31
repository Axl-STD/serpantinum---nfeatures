#!/usr/bin/env python3
"""v0.5.86 conservative read-only process/systemd resource correlation.

No resource is authenticated, terminated, adopted, or automatically cleaned.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

UNIT = re.compile(r'serpantinum-v0586-[0-9a-f]{20}\.service\Z')
HEX = re.compile(r'[0-9a-f]{32}\Z')
SAFE = dict(productionModified=False, automaticCleanup=False,
            atomicCrossStoreSnapshot=False, crashDurabilityVerified=False,
            historicalIdentityVerified=False, workerIdentityVerified=False,
            operationCompletionVerified=False, finalizationAuthorized=False,
            executionAuthorized=False, physicalExecutionEnabled=False,
            physicalRollbackVerified=False, commandsExecuted=0)


def proc_stat(raw):
    """Parse Linux /proc/PID/stat with comm containing spaces/parentheses."""
    if not isinstance(raw, str):
        raise ValueError('invalid stat')
    m = re.fullmatch(r'([1-9][0-9]*) \((.*)\) ([A-Za-z]) (.*)', raw.strip(), re.S)
    if not m:
        raise ValueError('invalid stat framing')
    tail = m.group(4).split()
    if len(tail) < 19:
        raise ValueError('truncated stat')
    vals = (int(m.group(1)), int(tail[0]), int(tail[1]), int(tail[2]), int(tail[18]))
    if any(v < 0 for v in vals) or vals[0] == 0 or vals[4] == 0:
        raise ValueError('invalid process fields')
    return dict(pid=vals[0], ppid=vals[1], pgid=vals[2], sid=vals[3], startTicks=vals[4], state=m.group(3))


def read_process(pid, root='/proc'):
    if type(pid) is not int or pid <= 0:
        return None
    try:
        path = Path(root) / str(pid) / 'stat'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return None
            raw = os.read(fd, 8193)
            if len(raw) > 8192:
                return None
            parsed = proc_stat(raw.decode('utf-8'))
            return parsed if parsed['pid'] == pid else None
        finally:
            os.close(fd)
    except (OSError, ValueError, UnicodeError):
        return None


def assess(expected, first, second, unit_first=None, unit_second=None):
    out = dict(version='0.5.86', mode='read-only-resource-ownership',
               assessment='uncertain', processState='unknown', unitState='unknown',
               resourceOwnershipVerified=False, **SAFE)
    if type(expected) is not dict or set(expected) != {'pid', 'startTicks', 'pgid', 'sid'}:
        return out
    if any(type(expected[k]) is not int or expected[k] <= 0 for k in expected):
        return out
    if first is None and second is None:
        out['assessment'] = 'process-not-observed'
        return out
    if type(first) is not dict or type(second) is not dict:
        return out
    if first != second:
        out['assessment'] = 'contradictory-observations'
        return out
    if any(first.get(k) != expected[k] for k in expected):
        out['assessment'] = 'identity-mismatch'
        return out
    if first.get('state') == 'Z':
        out['assessment'] = 'zombie-observed'
        out['processState'] = 'zombie'
        return out
    if first.get('state') not in ('R', 'S', 'D', 'T', 't', 'I'):
        return out
    out['assessment'] = 'process-metadata-consistent-only'
    out['processState'] = 'observed'
    if unit_first is not None or unit_second is not None:
        if type(unit_first) is not dict or type(unit_second) is not dict or unit_first != unit_second:
            out['assessment'] = 'contradictory-unit-observations'
            return out
        name = unit_first.get('Id')
        invocation = unit_first.get('InvocationID')
        if not isinstance(name, str) or not UNIT.fullmatch(name) or not isinstance(invocation, str) or not HEX.fullmatch(invocation) or int(invocation, 16) == 0:
            out['assessment'] = 'unsafe-unit-identity'
            return out
        if unit_first.get('ActiveState') not in ('active', 'inactive'):
            out['assessment'] = 'uncertain-unit-state'
            return out
        out['unitState'] = unit_first['ActiveState']
        out['assessment'] = 'process-and-unit-metadata-consistent-only'
    return out


def snapshot(pid, *, root='/proc'):
    first = read_process(pid, root)
    second = read_process(pid, root)
    if first is None:
        return assess({}, first, second)
    return assess({k: first[k] for k in ('pid', 'startTicks', 'pgid', 'sid')}, first, second)


def report(mode, rows):
    return dict(version='0.5.86', mode=mode, selftest='passed',
                totalScenarios=len(rows), results=rows, systemdIntegration=False,
                resourceOwnershipVerified=False, **SAFE,
                limitations=['Two proc observations are not atomic or authenticated ownership',
                             'Unit metadata is injected; no systemd operation in this suite',
                             'No automatic cleanup, signals to existing processes, or physical rollback'])


def selftest():
    rows = []
    def check(name, cond):
        if not cond:
            raise AssertionError(name)
        rows.append(dict(scenario=name, passed=True))
    pid = os.getpid()
    current = read_process(pid)
    check('live-proc-readable', current is not None and current['pid'] == pid)
    check('live-proc-stable', snapshot(pid)['assessment'] == 'process-metadata-consistent-only')
    check('missing-proc-uncertain', read_process(999999999) is None)
    check('invalid-pid-zero', read_process(0) is None)
    check('invalid-pid-bool', read_process(True) is None)
    check('invalid-pid-negative', read_process(-1) is None)
    check('invalid-pid-string', read_process(str(pid)) is None)
    raw = f'{pid} (a b (c)) S 1 2 3 ' + ' '.join(['0'] * 15 + ['123'] + ['0'] * 5)
    check('comm-with-parentheses', proc_stat(raw)['startTicks'] == 123)
    for label, data in [('malformed', 'nonsense'), ('truncated', f'{pid} (x) S 1 2'),
                        ('zero-start', raw.replace(' 123 ', ' 0 '))]:
        try:
            proc_stat(data)
        except ValueError:
            check(label, True)
        else:
            raise AssertionError(label)
    exp = {k: current[k] for k in ('pid', 'startTicks', 'pgid', 'sid')}
    check('consistent', assess(exp, current, current)['assessment'] == 'process-metadata-consistent-only')
    check('changed-start', assess(exp, current, {**current, 'startTicks': current['startTicks'] + 1})['assessment'] == 'contradictory-observations')
    check('changed-pgid', assess(exp, current, {**current, 'pgid': current['pgid'] + 1})['assessment'] == 'contradictory-observations')
    check('changed-ppid', assess(exp, current, {**current, 'ppid': current['ppid'] + 1})['assessment'] == 'contradictory-observations')
    check('pid-reuse', assess(exp, {**current, 'startTicks': current['startTicks'] + 1}, {**current, 'startTicks': current['startTicks'] + 1})['assessment'] == 'identity-mismatch')
    check('missing-first', assess(exp, None, current)['assessment'] == 'uncertain')
    check('missing-second', assess(exp, current, None)['assessment'] == 'uncertain')
    check('both-missing', assess(exp, None, None)['assessment'] == 'process-not-observed')
    check('zombie', assess(exp, {**current, 'state': 'Z'}, {**current, 'state': 'Z'})['assessment'] == 'zombie-observed')
    unit = dict(Id='serpantinum-v0586-' + 'a'*20 + '.service', InvocationID='b'*32, ActiveState='active')
    check('matching-unit', assess(exp, current, current, unit, unit)['assessment'] == 'process-and-unit-metadata-consistent-only')
    check('unit-change', assess(exp, current, current, unit, {**unit, 'InvocationID': 'c'*32})['assessment'] == 'contradictory-unit-observations')
    check('unit-missing', assess(exp, current, current, unit, None)['assessment'] == 'contradictory-unit-observations')
    check('unit-name-invalid', assess(exp, current, current, {**unit, 'Id': 'ssh.service'}, {**unit, 'Id': 'ssh.service'})['assessment'] == 'unsafe-unit-identity')
    check('unit-invocation-zero', assess(exp, current, current, {**unit, 'InvocationID': '0'*32}, {**unit, 'InvocationID': '0'*32})['assessment'] == 'unsafe-unit-identity')
    check('unit-activating', assess(exp, current, current, {**unit, 'ActiveState': 'activating'}, {**unit, 'ActiveState': 'activating'})['assessment'] == 'uncertain-unit-state')
    check('never-authenticated', assess(exp, current, current, unit, unit)['resourceOwnershipVerified'] is False)
    check('no-execution', report('offline', rows)['commandsExecuted'] == 0)
    check('no-cleanup', report('offline', rows)['automaticCleanup'] is False)
    check('no-physical-authorization', report('offline', rows)['physicalExecutionEnabled'] is False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0586-') as tmp:
        fake = Path(tmp) / str(pid)
        fake.mkdir()
        (fake / 'stat').symlink_to('/proc/self/stat')
        check('symlink-rejected', read_process(pid, tmp) is None)
    return report('offline', rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest', 'inspect'))
    parser.add_argument('--pid', type=int)
    args = parser.parse_args()
    try:
        if args.command == 'selftest':
            output = selftest()
        else:
            output = snapshot(args.pid) if args.pid else assess({}, None, None)
        print(json.dumps(output, indent=2))
    except (AssertionError, ValueError, OSError) as exc:
        print(json.dumps({'selftest': 'failed', 'error': str(exc)[:160]}))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
