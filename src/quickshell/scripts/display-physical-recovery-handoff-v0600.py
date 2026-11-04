#!/usr/bin/env python3
"""Read-only handoff between staged obligations and restricted monitor recovery.

Never authorizes, arms, applies, or restores a physical display. A proposed
rollback command is diagnostic data only, not a capability or execution token.
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent

def load(filename, alias):
    p = HERE / filename
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

stage = load('display-physical-obligation-stage-v0600.py', 'handoff_stage')
service = load('display-physical-obligation-service-v0600.py', 'handoff_service')
transport = load('display-restricted-hyprland-transport-v0600.py', 'handoff_transport')
ipc = stage.ipc

class Blocked(RuntimeError):
    pass

def output(decision, **fields):
    return dict(version='0.6.0', mode='physical-recovery-handoff-readonly', decision=decision,
                watchdogPhysicalArmed=False, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **fields)

def read_token(root):
    with ipc.locked(root) as p:
        r, s = stage.validate(p)
        m = ipc.read(p, stage.NAME)
        if m != stage.manifest(r, s):
            raise Blocked('staging mismatch')
        if r.get('state') != 'ARMED' or r.get('phase') != 'NONE' or type(r.get('generation')) is not int:
            raise Blocked('not pre-effect')
        if time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= r['deadline_boottime_ns']:
            raise Blocked('expired')
        if (p / 'physical-watchdog-rehearsal.json').exists():
            raise Blocked('lab receipt collision')
        token = (r['id'], r['generation'], r['boot_id'], s['snapshot_sha256'], s['identity']['instance'])
        baseline = json.loads(json.dumps(s['snapshot']))
        target = r['target']
    return token, baseline, target

def capture(instance, runner=subprocess.run):
    env = os.environ.copy()
    for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE'):
        env.pop(key, None)
    env['XDG_RUNTIME_DIR'] = '/run/user/' + str(os.getuid())
    def call(args):
        p = runner(args, capture_output=True, text=True, timeout=8, check=True, env=env)
        return json.loads(p.stdout)
    instances = call(['hyprctl', 'instances', '-j'])
    if not isinstance(instances, list) or len(instances) != 1 or instances[0].get('instance') != instance:
        raise Blocked('compositor identity changed')
    return call(['hyprctl', '-i', instance, 'monitors', '-j'])

def evaluate(root, observer=capture):
    token, baseline, target = read_token(root)
    expected = transport.snapshot(baseline, target)
    if expected['mode'] != 'original':
        raise Blocked('baseline not original')
    monitors = observer(token[4])
    current = transport.snapshot(monitors, target)
    mode = transport.compare(expected, current)
    # A second locked read closes the observe-then-check generation race.
    again, _, _ = read_token(root)
    if token != again:
        raise Blocked('concurrent transaction change')
    if mode == 'original':
        return output('ORIGINAL_OBSERVED_NOT_PHYSICAL_ROLLBACK', transactionId=token[0],
                      generation=token[1], target=target, observedMode='ORIGINAL')
    if mode != 'candidate':
        raise Blocked('unknown mode')
    args = transport.command(token[4], target, 'original', expected['geometry'])
    return output('ROLLBACK_PROPOSAL_ONLY_NOT_AUTHORIZED', transactionId=token[0],
                  generation=token[1], target=target, observedMode='CANDIDATE',
                  proposedRollbackArgs=args)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('inspect',))
    ap.add_argument('root')
    a = ap.parse_args()
    try:
        value = evaluate(a.root)
        code = 0
    except (Blocked, stage.Blocked, service.Blocked, transport.Denied, ipc.Denied,
            OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        value = output('BLOCK_AND_INVESTIGATE', errorType=type(exc).__name__)
        code = 2
    print(json.dumps(value, sort_keys=True), flush=True)
    return code

if __name__ == '__main__':
    sys.exit(main())
