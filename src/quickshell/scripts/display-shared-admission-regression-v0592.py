#!/usr/bin/env python3
"""v0.5.92: disposable shared-admission and delegated-fd regression."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import display_runtime_admission_v0592 as lock

def load(name):
    path = HERE / name
    spec = importlib.util.spec_from_file_location('reg_' + name.replace('-', '_'), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def selftest():
    results = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        results.append({'scenario': name, 'passed': True})
    with tempfile.TemporaryDirectory(prefix='serpantinum-v0592-admission-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        oldroot = os.environ.get('XDG_RUNTIME_DIR')
        oldfd = os.environ.pop(lock.ENV, None)
        os.environ['XDG_RUNTIME_DIR'] = str(root)
        path = root / lock.NAME
        def child(script, pass_fds=()):
            return subprocess.run([sys.executable, '-c', script], pass_fds=pass_fds,
                                  capture_output=True, text=True, timeout=8)
        probe = '''import sys;sys.path.insert(0,sys.argv[1]);import display_runtime_admission_v0592 as m
try:
 with m.admission(): print('ACQUIRED')
except m.AdmissionError: sys.exit(17)'''
        probe = probe.replace('sys.argv[1]', repr(str(HERE)))
        try:
            check('private-root', root.stat().st_mode & 0o077 == 0)
            check('offline-no-admission-env', lock.ENV not in os.environ)
            with lock.admission() as fd:
                check('lock-file-created', path.is_file())
                check('private-lock', path.stat().st_mode & 0o077 == 0)
                check('owner-env-descriptor', os.environ.get(lock.ENV) == str(fd))
                check('descriptor-matches-path', os.fstat(fd).st_ino == path.stat().st_ino)
                check('child-fds-exact', lock.child_fds() == (fd,))
                # The child without the actual descriptor must fail, not bypass using env alone.
                check('environment-only-rejected', child(probe).returncode == 17)
                check('unrelated-child-rejected', child(probe, ()).returncode == 17)
                check('delegated-child-accepted', child(probe, (fd,)).returncode == 0)
                check('parent-lock-survives-child', child(probe).returncode == 17)
                with lock.admission() as inherited:
                    check('same-process-delegation', inherited == fd)
                check('parent-lock-retained', child(probe).returncode == 17)
            check('environment-restored', lock.ENV not in os.environ)
            check('lock-file-preserved', path.exists())
            check('lock-reacquired', child(probe).returncode == 0)
            os.environ[lock.ENV] = '999999'
            try:
                with lock.admission(): pass
            except lock.AdmissionError:
                check('forged-fd-rejected', True)
            else:
                check('forged-fd-rejected', False)
            os.environ.pop(lock.ENV)
            fake = root / 'other'
            fake.write_text('x')
            otherfd = os.open(fake, os.O_RDWR)
            try:
                os.environ[lock.ENV] = str(otherfd)
                try:
                    with lock.admission(): pass
                except lock.AdmissionError:
                    check('wrong-inode-rejected', True)
                else:
                    check('wrong-inode-rejected', False)
            finally:
                os.environ.pop(lock.ENV)
                os.close(otherfd)
            # The direct legacy coordinator and the top-level gate now share one inode.
            legacy = load('display-consolidated-regression-gate-v05805.py')
            global_gate = load('display-global-runtime-gate-v0591.py')
            check('same-global-lock-name', global_gate.LOCK_NAME == lock.NAME)
            check('same-legacy-lock-name', legacy.RUNTIME_LOCK.name == lock.NAME)
            with global_gate.global_lock(root):
                check('legacy-direct-contended', child(probe).returncode == 17)
            with legacy.runtime_exclusive():
                check('legacy-direct-locked', child(probe).returncode == 17)
            check('legacy-release-recovered', child(probe).returncode == 0)
            # Direct combined gate must hold the lock across both stages, even with an injected runner.
            combined = load('display-evidence-security-gate-v0590.py')
            check('combined-runner-fd-propagation', 'child_fds' in combined.runner.__code__.co_names)
            check('combined-runtime-admission', 'admission' in combined.execute.__code__.co_names)
            identity = load('display-identity-evidence-gate-v0589.py')
            check('identity-uses-legacy-provider', 'runtime_exclusive' in identity.runtime_lock.__code__.co_names)
            check('no-physical-execution', True)
        finally:
            if oldroot is None:
                os.environ.pop('XDG_RUNTIME_DIR', None)
            else:
                os.environ['XDG_RUNTIME_DIR'] = oldroot
            if oldfd is None:
                os.environ.pop(lock.ENV, None)
            else:
                os.environ[lock.ENV] = oldfd
    return {'version': '0.5.92', 'mode': 'offline-shared-admission',
            'selftest': 'passed', 'totalScenarios': len(results), 'results': results,
            'systemdIntegration': False, 'productionModified': False,
            'automaticCleanup': False, 'resourceOwnershipVerified': False,
            'universalRuntimeExclusionVerified': False,
            'authenticatedDelegationImplemented': False,
            'executionAuthorized': False, 'physicalExecutionEnabled': False,
            'physicalRollbackVerified': False, 'commandsExecuted': 0,
            'limitations': ['Verified cooperating entrypoints only, not all possible external callers',
                            'No real systemd runtime executed by this suite',
                            'Inherited fd is a capability, not independent worker authentication']}

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        sys.exit(2)
    try:
        print(json.dumps(selftest(), indent=2))
    except Exception as exc:
        print(json.dumps({'selftest':'failed','error':repr(exc)}))
        sys.exit(1)
