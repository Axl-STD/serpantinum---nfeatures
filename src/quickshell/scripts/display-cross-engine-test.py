#!/usr/bin/env python3
"""Serpantinum v0.5.29: isolated cross-engine systemd tests. NO physical execution."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
FILES = ('display-sealed-watchdog.py', 'display-rollback-engine.py',
         'display-physical-rollback-engine.py', 'display-recovery-bundle.py',
         'display-restore-plan.py', 'display-restore-verify.py',
         'display-watchdog-audit.py', 'display-authorized-cleanup.py')
CASES = ('simulation-fails', 'rehearsal-fails', 'both-fail', 'both-pass')


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / filename)
    if spec is None or spec.loader is None:
        raise ValueError('Missing dependency ' + filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    engine = load('display-rollback-engine.py', 'fixture_0529')
    return {'schemaVersion': 1, 'outputs': engine.fixtures()}


def prepare(root):
    bundle = load('display-recovery-bundle.py', 'bundle_prepare_0529')
    watchdog = load('display-sealed-watchdog.py', 'watch_prepare_0529')
    approved = {'phase': 'prepared-read-only', 'structurallyValid': True,
                'nativeConfigParserAccepted': True, 'safeToExecuteAutomatically': False}
    prepared = bundle.prepare(Path('/unused'), root, lambda _: approved, lambda: fixture())
    folder = bundle.runtime_dir(root)
    token = prepared['transactionId']
    unit = 'serpantinum-display-v0514-' + token + '.service'
    with bundle.locked(folder):
        bundle.atomic_write(watchdog.state_path(folder), {
            'version': '0.5.14', 'mode': 'simulation-only', 'phase': 'starting',
            'transactionId': token, 'unit': unit, 'pending': True,
            'deadline': time.time() + 2.5, 'timeoutSeconds': 2.5,
            'watchdogReady': False, 'integrityVerified': True,
            'physicalRollbackAttempted': False, 'physicalRollbackVerified': False})
    return token, unit


def worker(root, token, scenario):
    if scenario not in CASES:
        raise ValueError('Invalid scenario')
    watchdog = load('display-sealed-watchdog.py', 'watch_worker_0529')
    sim_original = watchdog.simulate_rollback
    rehearsal_original = watchdog.rehearse_rollback
    def simulation(saved, current):
        return sim_original(saved, current, runner=lambda _: scenario not in ('simulation-fails', 'both-fail'))
    def rehearsal(saved, current):
        return rehearsal_original(saved, current, runner=lambda _: scenario not in ('rehearsal-fails', 'both-fail'))
    watchdog.simulate_rollback = simulation
    watchdog.rehearse_rollback = rehearsal
    watchdog.watch(token, root=root, capture=fixture)


def assess(root, scenario, unit):
    bundle = load('display-recovery-bundle.py', 'bundle_assess_0529')
    watchdog = load('display-sealed-watchdog.py', 'watch_assess_0529')
    auditor = load('display-watchdog-audit.py', 'audit_assess_0529')
    cleanup = load('display-authorized-cleanup.py', 'cleanup_assess_0529')
    folder = bundle.runtime_dir(root)
    with bundle.locked(folder):
        payload = bundle.read_bundle(folder)
        state = watchdog.read_state(folder)
    if not isinstance(payload, dict) or payload.get('id') != state.get('transactionId'):
        raise AssertionError('Missing or mismatched bundle')
    if state.get('phase') != 'snapshot-equivalent' or state.get('pending') is not False:
        raise AssertionError('Unexpected terminal state: ' + str(state.get('error')))
    expected_sim = 'rollback-simulated-failed' if scenario in ('simulation-fails', 'both-fail') else 'rollback-simulated-verified'
    expected_reh = 'rollback-rehearsal-failed' if scenario in ('rehearsal-fails', 'both-fail') else 'rollback-rehearsal-verified'
    if state.get('rollbackSimulationPhase') != expected_sim or state.get('rollbackRehearsalPhase') != expected_reh:
        raise AssertionError('Unexpected engine phases: ' + json.dumps(state))
    classification, healthy = auditor.classify(state, True, 'inactive')
    should_pass = scenario == 'both-pass'
    if healthy is not should_pass:
        raise AssertionError('Auditor safety mismatch: ' + classification)
    if should_pass and classification != 'completed-observable':
        raise AssertionError('Positive control not accepted: ' + classification)
    if not should_pass and classification not in ('rollback-simulation-failed', 'rollback-rehearsal-failed'):
        raise AssertionError('Failed engine hidden: ' + classification)
    if state.get('physicalRollbackVerified') is not False or state.get('commandsExecuted') != 0:
        raise AssertionError('Physical safety flags violated')
    if should_pass:
        cleaned = cleanup.cleanup(root)
        if cleaned.get('phase') != 'completed-cleaned' or bundle.read_bundle(folder) is not None:
            raise AssertionError('Positive cleanup failed')
        rejected = False
        preserved = False
    else:
        rejected = False
        try:
            cleanup.cleanup(root)
        except ValueError:
            rejected = True
        with bundle.locked(folder):
            preserved = bundle.read_bundle(folder) is not None
        if not rejected or not preserved:
            raise AssertionError('Failed evidence was not protected')
    return {'scenario': scenario, 'transactionId': state['transactionId'],
            'rollbackSimulationPhase': expected_sim, 'rollbackRehearsalPhase': expected_reh,
            'assessment': classification, 'healthy': healthy, 'cleanupRejected': rejected,
            'evidencePreserved': preserved, 'commandsExecuted': 0, 'physicalRollbackVerified': False}


def selftest():
    watchdog = load('display-sealed-watchdog.py', 'watch_test_0529')
    auditor = load('display-watchdog-audit.py', 'audit_test_0529')
    sim_good = watchdog.simulation_fields({'phase': 'rollback-verified', 'simulatedRollbackVerified': True})
    sim_bad = watchdog.simulation_fields({'phase': 'rollback-failed', 'simulatedRollbackVerified': False})
    reh_good = watchdog.rehearsal_fields({'phase': 'rollback-rehearsal-verified',
                                         'simulatedRollbackVerified': True,
                                         'physicalRollbackVerified': False, 'commandsExecuted': 0})
    reh_bad = watchdog.rehearsal_fields({'phase': 'rollback-rehearsal-failed',
                                        'simulatedRollbackVerified': False,
                                        'physicalRollbackVerified': False, 'commandsExecuted': 0})
    for scenario in CASES:
        state = {'phase': 'snapshot-equivalent', 'pending': False, 'equivalentObservableState': True}
        state.update(sim_bad if scenario in ('simulation-fails', 'both-fail') else sim_good)
        state.update(reh_bad if scenario in ('rehearsal-fails', 'both-fail') else reh_good)
        classification, healthy = auditor.classify(state, True, 'inactive')
        assert healthy is (scenario == 'both-pass'), (scenario, classification)
    return {'selftest': 'passed', 'scenarios': 4, 'systemdIntegration': 'not-run',
            'commandsExecuted': 0, 'physicalRollbackVerified': False}


def run():
    results = []
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0529-', dir=runtime) as temp:
        os.chmod(temp, 0o700)
        sandbox = Path(temp)
        for filename in FILES:
            shutil.copy2(HERE / filename, sandbox / filename)
        shutil.copy2(Path(__file__), sandbox / Path(__file__).name)
        for scenario in CASES:
            root = sandbox / scenario
            root.mkdir(mode=0o700)
            token, unit = prepare(root)
            cmd = ['systemd-run', '--user', '--unit=' + unit[:-8], '--collect', '--quiet',
                   sys.executable, str(sandbox / Path(__file__).name), '_worker',
                   '--root', str(root), '--token', token, '--scenario', scenario]
            subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
            watchdog = load('display-sealed-watchdog.py', 'watch_poll_0529')
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                state = watchdog.status(root)
                if state.get('pending') is False:
                    break
                time.sleep(.15)
            else:
                raise RuntimeError('Worker timeout: ' + scenario)
            until = time.monotonic() + 5
            while time.monotonic() < until and watchdog.active(unit):
                time.sleep(.1)
            if watchdog.active(unit):
                raise RuntimeError('Worker still active: ' + scenario)
            results.append(assess(root, scenario, unit))
    return {'version': '0.5.29', 'mode': 'isolated-systemd-cross-engine-test',
            'selftest': 'passed', 'scenarios': results,
            'commandsExecuted': 0, 'physicalRollbackVerified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['selftest', 'run', '_worker'])
    p.add_argument('--root', type=Path)
    p.add_argument('--token')
    p.add_argument('--scenario', choices=CASES)
    args = p.parse_args()
    if args.command == '_worker':
        if args.root is None or args.token is None or args.scenario is None:
            raise ValueError('Missing worker arguments')
        worker(args.root, args.token, args.scenario)
        return 0
    print(json.dumps(selftest() if args.command == 'selftest' else run(), indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'physicalRollbackVerified': False}), file=sys.stderr)
        sys.exit(2)
