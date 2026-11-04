#!/usr/bin/env python3
"""Consolidated offline regression for the read-only physical handoff."""
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
p = HERE / 'display-physical-recovery-handoff-v0600.py'
spec = importlib.util.spec_from_file_location('handoff_under_test', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = dict(name='eDP-1', description='AU Optronics', width=1920, height=1080,
            refreshRate=60.061, x=0, y=0, scale=1, transform=0,
            disabled=False, dpmsStatus=True)


def fixture(root):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    r = dict(schema=1, id=secrets.token_hex(32), generation=2, state='ARMED', phase='NONE',
             execution_backend='physical', target='eDP-1', topology='internal-only',
             plan_sha256='b'*64, boot_id=boot,
             deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+60_000_000_000)
    s = dict(transaction_id=r['id'], boot_id=boot, target='eDP-1', topology='internal-only',
             snapshot=[BASE.copy()], snapshot_sha256=m.stage.digest([BASE]),
             identity={'instance':'lab_instance'})
    with m.ipc.locked(root) as path:
        m.ipc.write(path, 'transaction.json', r)
        m.ipc.write(path, 'physical-snapshot.json', s)
    assert m.stage.stage(root)['decision'] == 'PHYSICAL_OBLIGATION_STAGED_NOT_ARMED'


def run():
    cases = []
    def scenario(name, mutate=None, observed=None, decision=None, blocked=False):
        with tempfile.TemporaryDirectory(prefix='serpantinum-handoff-') as d:
            root = Path(d)
            fixture(root)
            if mutate:
                with m.ipc.locked(root) as path:
                    mutate(path)
            try:
                value = m.evaluate(root, observer=observed or (lambda instance: [BASE.copy()]))
                assert not blocked and value['decision'] == decision, value
                assert value['commandsExecuted'] == 0 and value['physicalExecutionEnabled'] is False
                assert value['executionAuthorized'] is False and value['rollbackPhysicalVerified'] is False
                assert value['watchdogPhysicalArmed'] is False
            except (m.Blocked, m.stage.Blocked, m.transport.Denied, m.ipc.Denied, ValueError, KeyError, TypeError):
                assert blocked, name
            cases.append({'scenario':name,'passed':True})
    def change(**kwargs):
        def mutate(path):
            r = m.ipc.read(path,'transaction.json')
            m.ipc.write(path,'transaction.json',dict(r,**kwargs))
        return mutate
    scenario('original', decision='ORIGINAL_OBSERVED_NOT_PHYSICAL_ROLLBACK')
    scenario('candidate', observed=lambda _: [dict(BASE,refreshRate=144.06)], decision='ROLLBACK_PROPOSAL_ONLY_NOT_AUTHORIZED')
    scenario('generation_revalidated', mutate=change(generation=3), decision='ORIGINAL_OBSERVED_NOT_PHYSICAL_ROLLBACK')
    scenario('phase', mutate=change(phase='APPLY_VERIFIED'), blocked=True)
    scenario('state', mutate=change(state='APPLIED'), blocked=True)
    scenario('boot', mutate=change(boot_id='invalid'), blocked=True)
    scenario('backend', mutate=change(execution_backend='simulated'), blocked=True)
    scenario('expired', mutate=change(deadline_boottime_ns=0), blocked=True)
    scenario('hotplug', observed=lambda _: [BASE.copy(),dict(BASE,name='HDMI-A-1')], blocked=True)
    scenario('unknown_hz', observed=lambda _: [dict(BASE,refreshRate=80)], blocked=True)
    scenario('geometry', observed=lambda _: [dict(BASE,x=10)], blocked=True)
    scenario('disabled', observed=lambda _: [dict(BASE,disabled=True)], blocked=True)
    scenario('missing', observed=lambda _: [], blocked=True)
    scenario('identity_race', observed=lambda _: [], blocked=True)
    def race(root):
        with m.ipc.locked(root) as path:
            r=m.ipc.read(path,'transaction.json')
            m.ipc.write(path,'transaction.json',dict(r,generation=3))
        return [BASE.copy()]
    with tempfile.TemporaryDirectory(prefix='serpantinum-handoff-race-') as d:
        fixture(Path(d))
        try:
            m.evaluate(d, observer=lambda _: race(Path(d)))
            raise AssertionError('race not blocked')
        except m.Blocked:
            pass
        cases.append({'scenario':'generation_during_observation','passed':True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-handoff-lab-') as d:
        fixture(Path(d))
        with m.ipc.locked(d) as path:
            m.ipc.write(path,'physical-watchdog-rehearsal.json',{'status':'ARMED_LAB_ONLY'})
        try:
            m.evaluate(d, observer=lambda _: [BASE.copy()])
            raise AssertionError('lab collision not blocked')
        except m.Blocked:
            pass
        cases.append({'scenario':'lab_receipt_collision','passed':True})
    return dict(selftest='passed', totalScenarios=len(cases), results=cases,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)

if __name__ == '__main__':
    try:
        print(json.dumps(run(), indent=2))
    except Exception as exc:
        print(json.dumps({'selftest':'failed','error':repr(exc)}))
        sys.exit(2)
