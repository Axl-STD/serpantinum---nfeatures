#!/usr/bin/env python3
"""Offline crash matrix against the real BOOTTIME bridge. Never invokes display APIs.

Only child processes created by this test are killed. UNCERTAIN remains fail-closed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent

def load(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('unsafe dependency: ' + name)
    spec = importlib.util.spec_from_file_location(alias, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sim = load('display-recovery-simulator-v0600.py', 'crash_matrix_sim')
temporal = load('display-temporal-schema3-v0600.py', 'crash_matrix_temporal')
bridge_path = HERE / 'display-portable-runtime-bridge-v0600.py'

def setup(root):
    root.mkdir(mode=0o700)
    sim.initialize(root)
    r = sim.controller.prepare(root, sim.BASE, time.time_ns() + 4_000_000_000)
    a = sim.controller.transition(root, r['id'], r['generation'], 'ARMED')
    applied = sim.apply(root, r['id'], a['generation'])
    assert applied['state'] == 'APPLIED'
    temporal.bind(root)
    side = json.loads((root / 'temporal-schema3.json').read_text())
    while time.clock_gettime_ns(time.CLOCK_BOOTTIME) < side['deadline_boottime_ns']:
        time.sleep(0.025)

def run_bridge(root):
    p = subprocess.run([sys.executable, '-B', str(bridge_path), str(root)], capture_output=True, text=True, timeout=15)
    return p.returncode, json.loads(p.stdout)

def child(root, point):
    bridge = load('display-portable-runtime-bridge-v0600.py', 'crash_matrix_bridge')
    if point == 'after-due-write':
        original = bridge.controller._write
        def injected(path, record):
            result = original(path, record)
            if record['state'] == 'ROLLBACK_REQUIRED':
                os.kill(os.getpid(), signal.SIGKILL)
            return result
        bridge.controller._write = injected
    elif point == 'after-uncertain-write':
        original = bridge.sim.controller._write
        def injected(path, record):
            result = original(path, record)
            if record['state'] == 'UNCERTAIN':
                os.kill(os.getpid(), signal.SIGKILL)
            return result
        bridge.sim.controller._write = injected
    elif point == 'after-simulated-effect':
        original = bridge.sim._write_observed
        def injected(path, value):
            result = original(path, value)
            if value == bridge.sim.BASE:
                os.kill(os.getpid(), signal.SIGKILL)
            return result
        bridge.sim._write_observed = injected
    else:
        raise RuntimeError('invalid point')
    bridge.execute(root)
    raise RuntimeError('crash point not reached')

def scenario(root, point):
    setup(root)
    proc = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child', str(root), point], capture_output=True, text=True, timeout=15)
    assert proc.returncode == -signal.SIGKILL, (point, proc.returncode, proc.stderr)
    before = sim.inspect(root)
    if point == 'after-due-write':
        assert before['recovery']['state'] == 'ROLLBACK_REQUIRED'
        assert before['simulatedMonitor'] == sim.TARGET
        rc, recovered = run_bridge(root)
        assert rc == 0 and recovered['decision'] == 'SIMULATED_ROLLBACK_VERIFIED'
        assert sim.inspect(root)['simulatedMonitor'] == sim.BASE
        rc2, terminal = run_bridge(root)
        assert rc2 == 0 and terminal['decision'] == 'TERMINAL_NO_ACTION'
        return ['SIGKILL_AFTER_DUE', 'RECOVERABLE_PENDING', 'TAKEOVER_VERIFIED', 'TERMINAL_IDEMPOTENT']
    assert before['recovery']['state'] == 'UNCERTAIN'
    if point == 'after-uncertain-write':
        assert before['simulatedMonitor'] == sim.TARGET
    else:
        assert before['simulatedMonitor'] == sim.BASE
    rc, blocked = run_bridge(root)
    assert rc != 0 and blocked['decision'] == 'BLOCK_UNCERTAIN', blocked
    assert sim.inspect(root)['recovery']['state'] == 'UNCERTAIN'
    return ['SIGKILL_DURING_ROLLBACK', 'UNCERTAIN_PERSISTED', 'AUTOMATIC_RETRY_BLOCKED', 'NO_FALSE_ROLLBACK']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', nargs=2, metavar=('ROOT', 'POINT'))
    args = parser.parse_args()
    if args.child:
        child(Path(args.child[0]), args.child[1])
        return 1
    results = []
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0600-crash-matrix-') as td:
        base = Path(td)
        for point in ('after-due-write', 'after-uncertain-write', 'after-simulated-effect'):
            for label in scenario(base / point, point):
                results.append({'scenario': point + ':' + label, 'passed': True})
    print(json.dumps({'version':'0.6.0','mode':'offline-bridge-real-process-crash-matrix',
        'selftest':'passed','totalScenarios':len(results),'results':results,
        'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,
        'rollbackPhysicalVerified':False}, indent=2))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({'selftest':'failed','error':type(exc).__name__ + ': ' + str(exc)}))
        sys.exit(2)
