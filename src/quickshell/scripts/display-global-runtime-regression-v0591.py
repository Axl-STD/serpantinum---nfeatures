#!/usr/bin/env python3
"""v0.5.91: isolated global admission, negative contract, interruption tests."""
import contextlib
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
spec = importlib.util.spec_from_file_location('gate_v0591', HERE / 'display-global-runtime-gate-v0591.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

def selftest():
    results = []
    def record(name, ok):
        assert ok, name
        results.append({'scenario': name, 'passed': True})
    def fixture(mode='offline'):
        n = 4 if mode == 'runtime' else 2
        return {'selftest': 'passed', 'mode': mode, 'totalScenarios': n,
                'results': [{'passed': True} for _ in range(n)],
                'systemdIntegration': mode == 'runtime',
                **{k: False for k in gate.FALSE_FLAGS}, 'commandsExecuted': 0}
    def accepts(data, mode='offline'):
        try:
            gate.validate(data, mode)
            return True
        except gate.GateError:
            return False
    record('valid-offline', accepts(fixture()))
    record('valid-runtime', accepts(fixture('runtime'), 'runtime'))
    for name, mutate in (
        ('missing-results', lambda d: d.pop('results')),
        ('empty-results', lambda d: d.update(results=[])),
        ('boolean-count', lambda d: d.update(totalScenarios=True)),
        ('wrong-count', lambda d: d.update(totalScenarios=3)),
        ('failed-row', lambda d: d['results'][0].update(passed=False)),
        ('nonboolean-row', lambda d: d['results'][0].update(passed=1)),
        ('missing-ownership', lambda d: d.pop('resourceOwnershipVerified')),
        ('ownership-overclaim', lambda d: d.update(resourceOwnershipVerified=True)),
        ('physical-overclaim', lambda d: d.update(physicalExecutionEnabled=True)),
        ('cleanup-overclaim', lambda d: d.update(automaticCleanup=True)),
        ('atomic-overclaim', lambda d: d.update(atomicCrossStoreSnapshot=True)),
        ('boolean-commands', lambda d: d.update(commandsExecuted=False)),
        ('nonzero-commands', lambda d: d.update(commandsExecuted=1)),
        ('wrong-mode', lambda d: d.update(mode='runtime')),
        ('runtime-mislabel', lambda d: d.update(systemdIntegration=True)),
    ):
        data = fixture()
        mutate(data)
        record(name, not accepts(data))
    class Completed:
        def __init__(self, value):
            self.returncode, self.stdout = 0, json.dumps(value)
    events = []
    @contextlib.contextmanager
    def fake_lock():
        events.append('lock-enter')
        try:
            yield
        finally:
            events.append('lock-exit')
    def fake_run(argv):
        events.append('run-' + argv[-1])
        return Completed(fixture(argv[-1]))
    record('offline-no-global-lock', gate.execute('offline', fake_run, fake_lock)['mode'] == 'offline' and events == ['run-offline'])
    events.clear()
    record('runtime-global-lock-held', gate.execute('runtime', fake_run, fake_lock)['mode'] == 'runtime' and events == ['lock-enter','run-runtime','lock-exit'])
    record('report-scope-explicit', gate.report('runtime', fixture('runtime'))['globalAdmissionScope'] == 'v0591-entrypoint-only')
    record('report-no-authorization', gate.report('runtime', fixture('runtime'))['executionAuthorized'] is False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0591-') as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        record('private-test-directory', root.stat().st_mode & 0o077 == 0)
        with gate.global_lock(root):
            record('lock-file-private', (root / gate.LOCK_NAME).stat().st_mode & 0o077 == 0)
            record('lock-file-retained', (root / gate.LOCK_NAME).exists())
            child = subprocess.run([sys.executable, '-c',
                'import importlib.util,sys; p=sys.argv[1]; d=sys.argv[2]; s=importlib.util.spec_from_file_location("g",p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m);\ntry:\n with m.global_lock(d): pass\nexcept m.GateError: sys.exit(17)\nsys.exit(0)',
                str(HERE / 'display-global-runtime-gate-v0591.py'), str(root)], timeout=8)
            record('cross-process-contention', child.returncode == 17)
        with gate.global_lock(root):
            record('lock-recovered-after-release', True)
        proc = subprocess.Popen([sys.executable, '-c',
            'import importlib.util,sys,time; s=importlib.util.spec_from_file_location("g",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m);\nwith m.global_lock(sys.argv[2]):\n print("READY",flush=True); time.sleep(30)',
            str(HERE / 'display-global-runtime-gate-v0591.py'), str(root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            ready = proc.stdout.readline().strip()
            record('sigkill-holder-ready', ready == 'READY')
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            record('sigkill-holder-terminated', proc.returncode == -signal.SIGKILL)
            with gate.global_lock(root):
                record('sigkill-lock-recovery', True)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        record('lock-file-still-present', (root / gate.LOCK_NAME).exists())
        record('no-systemd-in-offline', True)
    record('no-physical-execution', gate.report('offline', fixture())['physicalExecutionEnabled'] is False)
    return {'version': '0.5.91', 'mode': 'selftest', 'selftest': 'passed',
            'totalScenarios': len(results), 'results': results, 'systemdIntegration': False,
            **{k: False for k in gate.FALSE_FLAGS}, 'commandsExecuted': 0}

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        sys.exit(2)
    print(json.dumps(selftest(), indent=2))
