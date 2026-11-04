#!/usr/bin/env python3
"""Offline tests of staged supervisor liveness and fail-closed boundaries."""
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time
import sys

HERE = Path(__file__).resolve().parent
p = HERE / 'display-physical-obligation-service-v0600.py'
s = importlib.util.spec_from_file_location('svc_test', p)
svc = importlib.util.module_from_spec(s)
s.loader.exec_module(svc)
stage = svc.stage
ipc = svc.ipc

results = []
def case(name, fn):
    try:
        fn()
        results.append(dict(scenario=name, passed=True))
    except Exception as e:
        results.append(dict(scenario=name, passed=False, error=type(e).__name__ + ': ' + str(e)))

def fixture():
    tmp = tempfile.TemporaryDirectory(prefix='serpantinum-service-test-')
    root = Path(tmp.name)
    root.chmod(0o700)
    m = dict(name='eDP-1', description='AU Optronics', width=1920, height=1080,
             refreshRate=60.061, x=0, y=0, scale=1, transform=0, disabled=False, dpmsStatus=True)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    r = dict(schema=1, id=secrets.token_hex(32), generation=2, state='ARMED', phase='NONE',
             execution_backend='physical', target='eDP-1', topology='internal-only',
             plan_sha256='b'*64, boot_id=boot,
             deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+60_000_000_000)
    snap = dict(transaction_id=r['id'], boot_id=boot, target='eDP-1', topology='internal-only',
                snapshot=[m], snapshot_sha256=stage.digest([m]), identity={'instance':'lab_instance'})
    with ipc.locked(root) as path:
        ipc.write(path, 'transaction.json', r)
        ipc.write(path, 'physical-snapshot.json', snap)
    stage.stage(root)
    return tmp, root

def expect_block(mutator):
    tmp, root = fixture()
    try:
        with ipc.locked(root) as path:
            mutator(path)
        try:
            svc.check(root)
        except (svc.Blocked, stage.Blocked, ipc.Denied, OSError, ValueError, KeyError, TypeError):
            return
        raise AssertionError('unsafe state accepted')
    finally:
        tmp.cleanup()

def mutate(filename, key, value):
    def fn(path):
        obj = ipc.read(path, filename)
        obj[key] = value
        ipc.write(path, filename, obj)
    return fn

def good():
    tmp, root = fixture()
    try:
        result = svc.run(root, 2, 0.1)
        assert result['decision'] == 'SERVICE_LIVENESS_VERIFIED_NOT_ARMED'
        with ipc.locked(root) as path:
            rec = ipc.read(path, svc.RECEIPT)
            assert rec['status'] == 'SERVICE_ALIVE_NOT_ARMED'
            assert rec['transaction_id'] == ipc.read(path, 'transaction.json')['id']
        assert result['commandsExecuted'] == 0 and result['watchdogPhysicalArmed'] is False
    finally:
        tmp.cleanup()

case('heartbeat-and-identity', good)
case('wrong-transaction-state', lambda: expect_block(mutate('transaction.json', 'state', 'APPLIED')))
case('wrong-phase', lambda: expect_block(mutate('transaction.json', 'phase', 'APPLY_VERIFIED')))
case('generation-drift', lambda: expect_block(mutate('transaction.json', 'generation', 'bad')))
case('snapshot-digest', lambda: expect_block(mutate('physical-snapshot.json', 'snapshot_sha256', '0'*64)))
case('staged-status', lambda: expect_block(mutate(stage.NAME, 'status', 'PHYSICAL_ARMED')))
case('staged-instance', lambda: expect_block(mutate(stage.NAME, 'instance', 'other')))
case('boot-id', lambda: expect_block(mutate('transaction.json', 'boot_id', 'wrong')))
case('deadline-expired', lambda: expect_block(mutate('transaction.json', 'deadline_boottime_ns', 1)))
case('backend-simulation', lambda: expect_block(mutate('transaction.json', 'execution_backend', 'simulation')))

def receipt_collision():
    tmp, root = fixture()
    try:
        with ipc.locked(root) as path:
            ipc.write(path, 'physical-watchdog-rehearsal.json', {'status':'ARMED_LAB_ONLY'})
        try: svc.check(root)
        except svc.Blocked: return
        raise AssertionError('lab collision accepted')
    finally: tmp.cleanup()
case('lab-receipt-collision', receipt_collision)

def identity_changed():
    tmp, root = fixture()
    try:
        token = svc.heartbeat(root)
        with ipc.locked(root) as path:
            r = ipc.read(path, 'transaction.json')
            r['generation'] += 1
            ipc.write(path, 'transaction.json', r)
        try: svc.heartbeat(root, token)
        except svc.Blocked: return
        raise AssertionError('identity drift accepted')
    finally: tmp.cleanup()
case('heartbeat-generation-fence', identity_changed)

passed = all(r['passed'] for r in results)
print(json.dumps(dict(selftest='passed' if passed else 'failed', totalScenarios=len(results),
                      results=results, physicalExecutionEnabled=False,
                      executionAuthorized=False, commandsExecuted=0,
                      rollbackPhysicalVerified=False), indent=2))
sys.exit(0 if passed else 2)
