#!/usr/bin/env python3
"""v0.5.83: isolated coordinator signal, timeout, lock and recovery tests.

All child processes are disposable Python fixtures. No systemd or display commands.
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

def load():
    spec = importlib.util.spec_from_file_location('gate_v0583', HERE / 'display-consolidated-regression-gate-v05805.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def wait_file(path, proc, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists():
            return
        if proc.poll() is not None:
            raise AssertionError('fixture exited before ready: ' + str(proc.returncode))
        time.sleep(.02)
    raise AssertionError('fixture readiness timeout')

def state(pid):
    try:
        return Path('/proc/%d/stat' % pid).read_text().split(') ', 1)[1][0]
    except FileNotFoundError:
        return 'X'

def selftest():
    mod = load()
    results = []
    def record(name, ok):
        assert ok, name
        results.append({'scenario': name, 'passed': True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0583-') as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        old = mod.RUNTIME_LOCK
        mod.RUNTIME_LOCK = root / 'runtime.lock'
        try:
            record('private-root', root.stat().st_mode & 0o077 == 0)
            record('runtime-lock-not-present-initially', not mod.RUNTIME_LOCK.exists())
            with mod.runtime_exclusive():
                record('runtime-lock-private', mod.RUNTIME_LOCK.stat().st_mode & 0o077 == 0)
                contender = subprocess.run([sys.executable, '-c',
                    'import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDWR);\n'
                    'try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
                    'except BlockingIOError: sys.exit(7)\n'
                    'sys.exit(0)', str(mod.RUNTIME_LOCK)], capture_output=True, timeout=5)
                record('second-process-blocked', contender.returncode == 7)
            with mod.runtime_exclusive():
                record('lock-reacquired-after-release', True)
            record('lock-file-preserved', mod.RUNTIME_LOCK.exists())
            try:
                with mod.runtime_exclusive():
                    raise RuntimeError('injected')
            except RuntimeError:
                pass
            with mod.runtime_exclusive():
                record('lock-recovered-after-exception', True)
            # A separate coordinator process acquires the lock and exits by SIGKILL.
            ready = root / 'lock-ready'
            holder = subprocess.Popen([sys.executable, '-c',
                'import fcntl,os,sys,time; f=os.open(sys.argv[1],os.O_RDWR);'
                'fcntl.flock(f,fcntl.LOCK_EX);open(sys.argv[2],"w").close();time.sleep(30)',
                str(mod.RUNTIME_LOCK), str(ready)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait_file(ready, holder)
                try:
                    with mod.runtime_exclusive():
                        raise AssertionError('lock unexpectedly acquired')
                except mod.GateBusy:
                    record('external-holder-blocks-runtime', True)
            finally:
                holder.kill()
                holder.wait(timeout=5)
            with mod.runtime_exclusive():
                record('lock-recovered-after-sigkill', True)
            record('isolated-success', mod.run_isolated([sys.executable, '-c', 'print("ok")'], 3).stdout.strip() == 'ok')
            record('isolated-nonzero', mod.run_isolated([sys.executable, '-c', 'import sys;sys.exit(13)'], 3).returncode == 13)
            for sig in (signal.SIGTERM, signal.SIGINT):
                ready = root / ('ready-' + sig.name)
                parent = subprocess.Popen([sys.executable, '-c',
                    'import importlib.util,sys,os;'
                    's=importlib.util.spec_from_file_location("gate",sys.argv[1]);'
                    'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                    'm.run_isolated([sys.executable,"-c",'
                    '"import pathlib,time,sys;pathlib.Path(sys.argv[1]).touch();time.sleep(30)",'
                    'sys.argv[2]],timeout=25)', str(HERE/'display-consolidated-regression-gate-v05805.py'), str(ready)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    wait_file(ready, parent)
                    parent.send_signal(sig)
                    _, stderr = parent.communicate(timeout=8)
                    record('parent-' + sig.name.lower() + '-exits', parent.returncode != 0)
                    record('parent-' + sig.name.lower() + '-reports-interruption', b'GateInterrupted' in stderr)
                finally:
                    if parent.poll() is None:
                        parent.kill(); parent.communicate(timeout=5)
            try:
                mod.run_isolated([sys.executable, '-c', 'import time;time.sleep(20)'], timeout=.15)
            except mod.GateError:
                record('timeout-fails-closed', True)
            else:
                raise AssertionError('timeout accepted')
            record('post-timeout-recovery', mod.run_isolated([sys.executable, '-c', 'print(123)'], 3).returncode == 0)
            marker = root / 'descendant.pid'
            child_script = ('import subprocess,sys,time,pathlib;'
                'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);'
                'pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)')
            try:
                mod.run_isolated([sys.executable, '-c', child_script, str(marker)], timeout=1.2)
            except mod.GateError:
                record('descendant-timeout-rejected', True)
            else:
                raise AssertionError('descendant timeout accepted')
            record('descendant-marker-created', marker.exists())
            pid = int(marker.read_text())
            record('descendant-no-longer-running', state(pid) in ('Z', 'X'))
            record('post-descendant-recovery', mod.run_isolated([sys.executable, '-c', 'print(1)'], 3).returncode == 0)
            record('offline-first', [x[0] for x in mod.OFFLINE] == ['guarantee-matrix','strict-contract'])
            record('runtime-explicit', len(mod.RUNTIME) == 3)
            record('safe-report', mod.report('runtime', [])['physicalExecutionEnabled'] is False)
        finally:
            mod.RUNTIME_LOCK = old
    return {'version':'0.5.83','mode':'isolated-interruption-recovery',
        'selftest':'passed','totalScenarios':len(results),'results':results,
        'systemdIntegration':False,'productionModified':False,'automaticCleanup':False,
        'atomicCrossStoreSnapshot':False,'crashDurabilityVerified':False,
        'historicalIdentityVerified':False,'workerIdentityVerified':False,
        'operationCompletionVerified':False,'finalizationAuthorized':False,
        'executionAuthorized':False,'physicalExecutionEnabled':False,
        'physicalRollbackVerified':False,'commandsExecuted':0,
        'limitations':['Disposable subprocesses, not spontaneous systemd interruptions',
        'SIGKILL cannot be caught; locks are kernel-released, not a recovery proof for systemd',
        'Process group cleanup is best effort and zombie reaping is external']}

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    print(json.dumps(selftest(), indent=2))
