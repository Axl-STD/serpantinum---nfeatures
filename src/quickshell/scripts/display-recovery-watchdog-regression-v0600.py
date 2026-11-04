#!/usr/bin/env python3
"""Offline integration regression for recovery watchdog, no physical execution."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time

HERE = Path(__file__).resolve().parent

def load(name, key):
    spec = importlib.util.spec_from_file_location(key, HERE / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

c = load('display-physical-recovery-controller-v0600.py', 'controller_test')
w = load('display-recovery-watchdog-v0600.py', 'watchdog_test')
rows = []
def check(label, condition):
    rows.append(dict(scenario=label, passed=bool(condition)))
    if not condition:
        raise AssertionError(label)

snap = dict(name='eDP-1', width=1920, height=1080, refresh_mhz=60060,
            scale_milli=1000, transform=0)
with tempfile.TemporaryDirectory(prefix='serpantinum-watchdog-v0600-') as tmp:
    root = Path(tmp)
    root.chmod(0o700)
    future = time.time_ns() + 60_000_000_000
    check('no-state-blocks', w.decision(root)['decision'] == 'BLOCK_NO_RECOVERY_STATE')
    rec = c.prepare(root, snap, future)
    check('prepared', rec['state'] == 'PREPARED')
    check('prepared-waits', w.decision(root, future-1)['state'] == 'PREPARED')
    check('prepared-generation-stable', c.inspect(root)['generation'] == 1)
    check('before-deadline-no-rollback', w.decision(root, future-1)['decision'] == 'WAIT_OR_REQUIRE_OPERATOR')
    check('deadline-rolls-to-required', w.decision(root, future)['state'] == 'ROLLBACK_REQUIRED')
    check('deadline-generation', c.inspect(root)['generation'] == 2)
    check('required-blocks', w.decision(root, future+1)['decision'] == 'BLOCK_PHYSICAL_ROLLBACK_NOT_IMPLEMENTED')
    check('repeat-idempotent', c.inspect(root)['generation'] == 2)
    check('snapshot-preserved', c.inspect(root)['snapshot'] == snap)
    check('token-preserved', c.inspect(root)['id'] == rec['id'])
    check('no-physical', w.decision(root)['commandsExecuted'] == 0)
with tempfile.TemporaryDirectory(prefix='serpantinum-watchdog-v0600-') as tmp:
    root = Path(tmp); root.chmod(0o700)
    future = time.time_ns() + 60_000_000_000
    rec = c.prepare(root, snap, future)
    armed = c.transition(root, rec['id'], 1, 'ARMED')
    check('armed', armed['state'] == 'ARMED')
    check('armed-waits', w.decision(root, future-1)['state'] == 'ARMED')
    confirmed = c.transition(root, rec['id'], 2, 'UNCERTAIN')
    check('uncertain', confirmed['state'] == 'UNCERTAIN')
    check('uncertain-blocks', w.decision(root, future+1)['decision'] == 'BLOCK_AND_INVESTIGATE')
    check('uncertain-stable', c.inspect(root)['generation'] == 3)
with tempfile.TemporaryDirectory(prefix='serpantinum-watchdog-v0600-') as tmp:
    root = Path(tmp); root.chmod(0o700)
    future = time.time_ns() + 60_000_000_000
    rec = c.prepare(root, snap, future)
    armed = c.transition(root, rec['id'], 1, 'ARMED')
    check('armed2', armed['generation'] == 2)
    check('armed-expire', w.decision(root, future)['state'] == 'ROLLBACK_REQUIRED')
    check('armed-repeat', w.decision(root, future+2)['generation'] == 3)
    check('armed-no-physical', w.decision(root)['physicalExecutionEnabled'] is False)
with tempfile.TemporaryDirectory(prefix='serpantinum-watchdog-v0600-') as tmp:
    root = Path(tmp); root.chmod(0o700)
    future = time.time_ns() + 60_000_000_000
    rec = c.prepare(root, snap, future)
    armed = c.transition(root, rec['id'], 1, 'ARMED')
    check('armed3', armed['generation'] == 2)
    # APPLIED intentionally inaccessible; confirmation must not be fabricated.
    try:
        c.transition(root, rec['id'], 2, 'APPLIED')
    except c.RecoveryDenied:
        check('applied-blocked', True)
    else:
        check('applied-blocked', False)
    check('not-applied', c.inspect(root)['state'] == 'ARMED')
    check('expires', w.decision(root, future)['state'] == 'ROLLBACK_REQUIRED')
    try:
        c.transition(root, rec['id'], 3, 'ROLLED_BACK')
    except c.RecoveryDenied:
        check('false-rollback-blocked', True)
    else:
        check('false-rollback-blocked', False)
    check('still-required', c.inspect(root)['state'] == 'ROLLBACK_REQUIRED')
with tempfile.TemporaryDirectory(prefix='serpantinum-watchdog-v0600-') as tmp:
    root = Path(tmp); root.chmod(0o700)
    (root/'recovery.json').write_text('invalid')
    check('corruption-blocked', w.decision(root)['decision'] == 'BLOCK_AND_INVESTIGATE')
    check('corruption-error', w.decision(root)['errorType'] == 'RecoveryDenied')
    check('corruption-no-command', w.decision(root)['commandsExecuted'] == 0)

payload = dict(version='0.6.0', mode='offline-recovery-watchdog-regression',
               totalScenarios=len(rows), selftest='passed', results=rows,
               executionAuthorized=False, physicalExecutionEnabled=False,
               commandsExecuted=0)
print(json.dumps(payload, indent=2))
