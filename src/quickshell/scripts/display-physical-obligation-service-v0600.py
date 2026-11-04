#!/usr/bin/env python3
"""Persistent staged-obligation supervisor. No physical actions or authorization.

Run as a systemd user service or foreground from TTY3. A heartbeat is evidence
of liveness only, never evidence of physical recovery capability.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

HERE = Path(__file__).resolve().parent

def dependency(filename, alias):
    p = HERE / filename
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

stage = dependency('display-physical-obligation-stage-v0600.py', 'service_stage')
ipc = stage.ipc
RECEIPT = 'physical-supervisor-liveness.json'

class Blocked(RuntimeError):
    pass

def output(decision, **fields):
    return dict(version='0.6.0', mode='physical-staged-service', decision=decision,
                watchdogPhysicalArmed=False, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **fields)

def check(root):
    with ipc.locked(root) as p:
        r, s = stage.validate(p)
        m = ipc.read(p, stage.NAME)
        if m != stage.manifest(r, s):
            raise Blocked('staged obligation mismatch')
        if type(r.get('generation')) is not int or r['generation'] < 0:
            raise Blocked('invalid generation')
        if r.get('state') != 'ARMED' or r.get('phase') != 'NONE':
            raise Blocked('transaction no longer pre-effect')
        if time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= r['deadline_boottime_ns']:
            raise Blocked('deadline elapsed')
        if (p / 'physical-watchdog-rehearsal.json').exists():
            raise Blocked('laboratory receipt collision')
        return r['id'], r['generation'], m['snapshot_sha256'], m['instance']

def heartbeat(root, expected=None):
    token = check(root)
    if expected is not None and token != expected:
        raise Blocked('transaction identity or generation changed')
    with ipc.locked(root) as p:
        # Revalidate while holding the lock before recording process liveness.
        r, s = stage.validate(p)
        m = ipc.read(p, stage.NAME)
        if m != stage.manifest(r, s) or (r['id'], r['generation'], m['snapshot_sha256'], m['instance']) != token:
            raise Blocked('concurrent transaction change')
        ipc.write(p, RECEIPT, dict(schema=1, status='SERVICE_ALIVE_NOT_ARMED',
                  transaction_id=token[0], generation=token[1],
                  snapshot_sha256=token[2], instance=token[3],
                  boot_id=r['boot_id'], pid=os.getpid(),
                  boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)))
    return token

def run(root, iterations, interval):
    if not 1 <= iterations <= 300 or not 0.1 <= interval <= 10:
        raise Blocked('invalid bounds')
    token = None
    for i in range(iterations):
        token = heartbeat(root, token)
        if i + 1 < iterations:
            time.sleep(interval)
    return output('SERVICE_LIVENESS_VERIFIED_NOT_ARMED', transactionId=token[0], iterations=iterations)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'serve'))
    parser.add_argument('root')
    parser.add_argument('--iterations', type=int, default=1)
    parser.add_argument('--interval', type=float, default=1.0)
    a = parser.parse_args()
    try:
        if a.action == 'inspect':
            token = check(a.root)
            result = output('STAGED_OBLIGATION_VERIFIED_NOT_ARMED', transactionId=token[0])
        else:
            result = run(a.root, a.iterations, a.interval)
        status = 0
    except (Blocked, stage.Blocked, ipc.Denied, OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = output('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
        status = 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return status

if __name__ == '__main__':
    sys.exit(main())
