#!/usr/bin/env python3
"""Read-only integration harness for Serpantinum display transaction prototypes.

No display settings are applied. All transaction state lives in a temporary,
private test directory. No permanent service is installed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError('Missing controller: ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture(controller, fixture=None):
    if fixture is None:
        return controller.normalize(controller.monitors())
    data = json.loads(Path(fixture).read_text(encoding='utf-8'))
    if isinstance(data, dict) and 'outputs' in data:
        data = data['outputs']
    return controller.normalize(data)


def exercise(controller, simulator, state):
    names = [m['name'] for m in state]
    assert names and len(names) == len(set(names)), 'Invalid monitor snapshot'
    plan = {'outputs': [{'name': names[0]}]}
    changes = controller.validate_plan(plan, state)
    assert len(changes) == 1
    outcomes = {}
    with tempfile.TemporaryDirectory(prefix='serpantinum-v052-') as temp:
        directory = Path(temp)
        # No monitor mutations and no watchdog needed for deterministic cases.
        first = simulator.start(directory, 15, watchdog=False)
        try:
            simulator.start(directory, 15, watchdog=False)
        except ValueError:
            outcomes['concurrentStartRejected'] = True
        else:
            raise AssertionError('Concurrent transaction accepted')
        try:
            simulator.resolve(directory, 'invalid-token', 'confirm')
        except ValueError:
            outcomes['invalidTokenRejected'] = True
        else:
            raise AssertionError('Invalid token accepted')
        assert simulator.resolve(directory, first['id'], 'confirm')['phase'] == 'simulated-confirmed'
        outcomes['confirmation'] = True
        second = simulator.start(directory, 15, watchdog=False)
        assert simulator.resolve(directory, second['id'], 'rollback')['phase'] == 'simulated-rollback-manual'
        outcomes['manualRollback'] = True
        third = simulator.start(directory, 15, watchdog=False)
        with simulator.locked(directory):
            result = simulator.expire_if_needed(directory, third['deadline'] + 0.01)
        assert result['phase'] == 'simulated-rollback-timeout'
        outcomes['timeout'] = True
    return outcomes


def detached_watchdog_test(simulator):
    """Launch the real simulator CLI from a short-lived initiator process.

    The watchdog must update state after that process exits. Use an isolated
    XDG_RUNTIME_DIR so normal simulation state is untouched.
    """
    with tempfile.TemporaryDirectory(prefix='serpantinum-v052-watch-') as runtime:
        os.chmod(runtime, 0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=runtime)
        sim_path = HERE / 'display-transaction-sim.py'
        initiator = subprocess.run([sys.executable, str(sim_path), 'start', '--seconds', '5'],
                                   capture_output=True, text=True, env=env, timeout=8, check=True)
        pending = json.loads(initiator.stdout)
        assert pending['pending']
        # Initiator is gone; the watchdog must still complete on its own.
        deadline = time.monotonic() + 9
        result = None
        while time.monotonic() < deadline:
            probe = subprocess.run([sys.executable, str(sim_path), 'status'],
                                   capture_output=True, text=True, env=env, timeout=4, check=True)
            result = json.loads(probe.stdout)
            if not result['pending']:
                break
            time.sleep(0.25)
        assert result and result['phase'] == 'simulated-rollback-timeout', 'Watchdog did not expire'
        return {'initiatorExited': True, 'watchdogExpiryObserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['selftest', 'snapshot-check'])
    parser.add_argument('--fixture', type=Path, help='Optional offline hyprctl JSON fixture')
    parser.add_argument('--watchdog', action='store_true', help='Test detached watchdog with 5-second expiry')
    args = parser.parse_args()
    controller = load('display-transaction.py', 'serpantinum_display_controller')
    simulator = load('display-transaction-sim.py', 'serpantinum_display_simulator')
    state = capture(controller, args.fixture)
    output = {'version': '0.5.2', 'mode': 'simulation-only', 'outputs': len(state),
              'names': [m['name'] for m in state], 'physicalOutputsChanged': False,
              'persistentConfigChanged': False}
    if args.action == 'selftest':
        output['checks'] = exercise(controller, simulator, state)
        if args.watchdog:
            output['watchdog'] = detached_watchdog_test(simulator)
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
