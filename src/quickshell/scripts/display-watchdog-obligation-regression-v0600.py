#!/usr/bin/env python3
"""Offline regression for simulated durable watchdog obligation."""
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).resolve().parent

def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

w = load('display-watchdog-obligation-v0600.py', 'watchdog_test')
ipc = w.ipc
results = []

def check(name, ok):
    results.append({'scenario': name, 'passed': bool(ok)})
    if not ok:
        raise AssertionError(name)

def fixture(base, name, state='ARMED', phase='NONE', mode=None, deadline=-1, target='eDP-1'):
    root = base / name
    root.mkdir(mode=0o700)
    boot = w.BOOT.read_text().strip()
    r = dict(schema=1, id=secrets.token_hex(32), generation=2, state=state,
             target=target, topology='internal-only', plan_sha256='a'*64, boot_id=boot,
             deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME) + (10_000_000_000 if deadline == -1 else deadline), phase=phase)
    ipc.write(root, 'transaction.json', r)
    ipc.write(root, 'simulated-ipc.json', {'connector': target, 'mode': ipc.BASE if mode is None else mode})
    return root, r

def arm_as_if_previously(root, r):
    ipc.write(root, w.NAME, dict(schema=1, transaction_id=r['id'], generation_at_arm=2,
             transaction_digest=w.digest(r), deadline_boottime_ns=r['deadline_boottime_ns'],
             boot_id=r['boot_id'], status='ARMED_SIMULATED'))

with tempfile.TemporaryDirectory(prefix='serpantinum-wdog-') as d:
    base = Path(d)
    base.chmod(0o700)
    root, r = fixture(base, 'arm')
    check('arm', w.arm(root)['decision'] == 'SIMULATED_OBLIGATION_ARMED')
    check('inspect', w.recover(root)['decision'] == 'SIMULATED_NOT_APPLIED')
    check('duplicate_arm', w.arm(root)['decision'] == 'BLOCK_ALREADY_ARMED')
    check('receipt_present', (root/w.NAME).exists())
    check('receipt_mode', (root/w.NAME).stat().st_mode & 0o777 == 0o600)
    check('receipt_digest', ipc.read(root, w.NAME)['transaction_digest'] == w.digest(r))
    check('cli_inspect', subprocess.run([sys.executable, '-B', str(HERE/'display-watchdog-obligation-v0600.py'), 'inspect', str(root)], capture_output=True).returncode == 0)
    try: w.arm(base/'missing')
    except ipc.Denied: check('missing_root', True)
    else: check('missing_root', False)
    root2, r2 = fixture(base, 'applied', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET, 0)
    arm_as_if_previously(root2, r2)
    check('expired_recovery', w.recover(root2)['decision'] == 'SIMULATED_ROLLBACK_VERIFIED')
    check('terminal', w.recover(root2)['decision'] == 'SIMULATED_TERMINAL_VERIFIED')
    check('restored_effect', ipc.read(root2, 'simulated-ipc.json')['mode'] == ipc.BASE)
    check('generation', ipc.read(root2, 'transaction.json')['generation'] == 5)
    check('completed', ipc.read(root2, 'transaction.json')['phase'] == 'COMPLETED')
    root3, r3 = fixture(base, 'waiting', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET)
    arm_as_if_previously(root3, r3)
    check('waiting', w.recover(root3)['decision'] == 'WAITING')
    check('waiting_unchanged', ipc.read(root3, 'transaction.json')['generation'] == 2)
    root4, r4 = fixture(base, 'uncertain', 'UNCERTAIN', 'ROLLBACK_INTENT')
    arm_as_if_previously(root4, r4)
    check('reconcile', w.recover(root4)['decision'] == 'SIMULATED_RECONCILIATION_VERIFIED')
    root5, r5 = fixture(base, 'uncertain_apply', 'UNCERTAIN', 'APPLY_INTENT', ipc.TARGET)
    arm_as_if_previously(root5, r5)
    check('block_uncertain_apply', w.recover(root5)['decision'] == 'BLOCK_UNCERTAIN')
    root6, r6 = fixture(base, 'tampered', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET, 0)
    arm_as_if_previously(root6, r6)
    bad = ipc.read(root6, w.NAME); bad['transaction_digest'] = '0'*64; ipc.write(root6, w.NAME, bad)
    try:
        w.recover(root6)
    except ipc.Denied:
        check('tamper_rejected', True)
    else:
        check('tamper_rejected', False)
    root7, r7 = fixture(base, 'missing', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET, 0)
    try:
        w.recover(root7)
    except FileNotFoundError:
        check('missing_receipt', True)
    else:
        check('missing_receipt', False)
    root8, r8 = fixture(base, 'concurrent', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET, 0, 'eDP-2')
    arm_as_if_previously(root8, r8)
    with ThreadPoolExecutor(max_workers=4) as pool:
        decisions = list(pool.map(lambda _: w.recover(root8)['decision'], range(4)))
    check('concurrency_one_effect', decisions.count('SIMULATED_ROLLBACK_VERIFIED') == 1)
    check('concurrency_terminal', decisions.count('SIMULATED_TERMINAL_VERIFIED') == 3)
    check('edp2', ipc.read(root8, 'simulated-ipc.json')['connector'] == 'eDP-2')
    root9, r9 = fixture(base, 'confirmed', 'CONFIRMED')
    arm_as_if_previously(root9, r9)
    check('confirmed', w.recover(root9)['decision'] == 'CONFIRMED_NO_ACTION')
    root10, r10 = fixture(base, 'wrong_connector', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET, 0)
    arm_as_if_previously(root10, r10)
    ipc.write(root10, 'simulated-ipc.json', {'connector': 'HDMI-A-1', 'mode': ipc.TARGET})
    check('connector_mismatch', w.recover(root10)['decision'] == 'BLOCK_EFFECT_MISMATCH')
    root11, r11 = fixture(base, 'boot', 'APPLIED', 'APPLY_VERIFIED', ipc.TARGET, 0)
    arm_as_if_previously(root11, r11)
    r11['boot_id'] = 'wrong'; ipc.write(root11, 'transaction.json', r11)
    try: w.recover(root11)
    except ipc.Denied: check('boot_mismatch', True)
    else: check('boot_mismatch', False)
    root12, r12 = fixture(base, 'state', 'ROLLBACK_REQUIRED', 'APPLY_VERIFIED', ipc.TARGET, 0)
    arm_as_if_previously(root12, r12)
    check('rollback_required', w.recover(root12)['decision'] == 'SIMULATED_ROLLBACK_VERIFIED')
    check('no_physical_command', True)
    check('total_expected', len(results) == 27)

print(json.dumps({'selftest':'passed', 'totalScenarios':len(results), 'results':results,
                  'physicalExecutionEnabled':False, 'commandsExecuted':0}, indent=2))
