#!/usr/bin/env python3
"""Offline physical rollback rehearsal; no Hyprland mutations, no physical authorization.

Uses the existing IPC lock, transaction and physical snapshot sidecar. A mock
transport simulates an IPC mutation and readback. Never writes transaction.json.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    p = HERE / filename
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

snap = load('display-physical-snapshot-binding-v0600.py', 'rehearsal_snap')
ipc = snap.ipc

class Denied(RuntimeError):
    pass

def compare(baseline, observed, target):
    if not isinstance(observed, list) or len(observed) != len(baseline):
        raise Denied('topology drift')
    by_name = {}
    for m in observed:
        if not isinstance(m, dict) or any(k not in m for k in snap.FIELDS):
            raise Denied('incomplete observation')
        if m['name'] in by_name:
            raise Denied('duplicate connector')
        by_name[m['name']] = m
    if set(by_name) != {m['name'] for m in baseline}:
        raise Denied('connector drift')
    for old in baseline:
        now = by_name[old['name']]
        for key in snap.FIELDS:
            if key == 'refreshRate' and old['name'] == target:
                continue
            if type(now[key]) is not type(old[key]) or now[key] != old[key]:
                raise Denied('geometry or identity drift')
    hz = by_name[target]['refreshRate']
    if type(hz) not in (float, int):
        raise Denied('invalid frequency')
    if abs(hz - next(m['refreshRate'] for m in baseline if m['name'] == target)) < 0.12:
        return 'ORIGINAL'
    if abs(hz - 144.06) < 0.12:
        return 'CANDIDATE'
    raise Denied('unexpected frequency')

class MockTransport:
    def __init__(self, monitors, *, fail=False, ignore=False):
        self.monitors = json.loads(json.dumps(monitors))
        self.fail = fail
        self.ignore = ignore
        self.calls = 0

    def observe(self):
        return json.loads(json.dumps(self.monitors))

    def restore(self, target, hz):
        self.calls += 1
        if self.fail:
            raise OSError('mock IPC unavailable')
        if not self.ignore:
            for m in self.monitors:
                if m['name'] == target:
                    m['refreshRate'] = hz


def execute(root, capture, transport, *, require_due=True):
    # The mock effect is never used to certify physical rollback.
    with ipc.locked(root) as p:
        r = ipc.read(p, 'transaction.json')
        s = ipc.read(p, 'physical-snapshot.json')
        if not isinstance(r, dict) or not isinstance(s, dict):
            raise Denied('invalid record')
        if (s.get('schema') != 1 or r.get('id') != s.get('transaction_id') or
            r.get('boot_id') != s.get('boot_id') or r.get('target') != s.get('target') or
            r.get('topology') != s.get('topology') or
            type(r.get('generation')) is not int or
            r['generation'] < s.get('generation_at_binding', 10**9) or
            snap.digest(s.get('snapshot')) != s.get('snapshot_sha256')):
            raise Denied('transaction mismatch')
        baseline = snap.validate(s['snapshot'], s['target'])
        if baseline != s['snapshot'] or snap.identity(capture, s['target'], s['topology']) != s['identity']:
            raise Denied('snapshot or compositor mismatch')
        if r.get('state') not in ('APPLIED', 'ROLLBACK_REQUIRED', 'UNCERTAIN'):
            raise Denied('not pending')
        if r['state'] == 'UNCERTAIN' and r.get('phase') != 'ROLLBACK_INTENT':
            raise Denied('ambiguous application')
        if r['state'] in ('APPLIED', 'ROLLBACK_REQUIRED') and r.get('phase') != 'APPLY_VERIFIED':
            raise Denied('invalid phase')
        if require_due and r['state'] == 'APPLIED':
            import time
            if time.clock_gettime_ns(time.CLOCK_BOOTTIME) < r.get('deadline_boottime_ns', 0):
                raise Denied('deadline not due')
        before = compare(baseline, transport.observe(), s['target'])
        if before == 'CANDIDATE':
            hz = next(m['refreshRate'] for m in baseline if m['name'] == s['target'])
            transport.restore(s['target'], hz)
        after = compare(baseline, transport.observe(), s['target'])
        if after != 'ORIGINAL':
            raise Denied('mock restoration not observed')
        return {'version': '0.6.0', 'decision': 'MOCK_ROLLBACK_REHEARSED',
                'before': before, 'after': after, 'mockCalls': transport.calls,
                'transactionId': r['id'], 'generation': r['generation'],
                'physicalExecutionEnabled': False, 'executionAuthorized': False,
                'commandsExecuted': 0, 'rollbackPhysicalVerified': False}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--capture', required=True)
    ap.add_argument('--mock-monitors', required=True)
    args = ap.parse_args()
    try:
        capture = json.loads(Path(args.capture).read_text())
        monitors = json.loads(Path(args.mock_monitors).read_text())
        result = execute(Path(args.root), capture, MockTransport(monitors))
        code = 0
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        result = {'decision': 'BLOCK_AND_INVESTIGATE', 'errorType': type(exc).__name__,
                  'physicalExecutionEnabled': False, 'executionAuthorized': False,
                  'commandsExecuted': 0, 'rollbackPhysicalVerified': False}
        code = 2
    print(json.dumps(result, indent=2))
    return code

if __name__ == '__main__':
    raise SystemExit(main())
