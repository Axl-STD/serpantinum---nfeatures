#!/usr/bin/env python3
"""Offline-only coordinator-owned evidence worker admission.

An inherited flock FD is never passed to the worker. Broker ACK is evidence of
one registration, not authority to write journals or modify displays.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def load(name, key):
    path = HERE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe dependency')
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

owner = load('display-admission-owner-v0600.py', 'evidence_owner')
worker = load('display-worker-authority-v0600.py', 'evidence_worker')

class DelegationDenied(RuntimeError):
    pass


def worker_main(socket_path, operation):
    # Only a disposable evidence probe; no commands or journal writes.
    return 0 if worker.request(socket_path, operation) else 17


def run_probe(runtime_root, broker_root, operation='offline-probe', timeout=8):
    """Spawn a known fixture and authenticate its one-shot handshake.

    Never propagates the owner's lock descriptor or admission environment.
    """
    if operation != 'offline-probe':
        raise DelegationDenied('operation not allowlisted')
    runtime_root, broker_root = Path(runtime_root), Path(broker_root)
    if owner.ENV in os.environ:
        raise DelegationDenied('inherited admission is forbidden')
    proc = None
    try:
        with owner.owner_only(runtime_root):
            with worker.Broker(broker_root) as broker:
                env = os.environ.copy()
                env.pop(owner.ENV, None)
                env.pop('PYTHONPATH', None)
                proc = subprocess.Popen(
                    [sys.executable, '-B', str(Path(__file__).resolve()),
                     '_fixture', str(broker.path), operation],
                    env=env, close_fds=True, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    start_new_session=True, text=True)
                broker.register(proc.pid, operation)
                result = broker.serve_once()
                try:
                    _, stderr = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise DelegationDenied('worker timeout')
                if not result['accepted'] or result['peerPid'] != proc.pid or proc.returncode != 0:
                    raise DelegationDenied('worker not authorized: ' + stderr[:100])
                return {'selftest': 'passed', 'mode': 'offline-delegated-evidence',
                        'workerAuthenticatedForHandshake': True,
                        'authenticatedDelegationImplemented': False,
                        'executionAuthorized': False, 'physicalExecutionEnabled': False,
                        'commandsExecuted': 0}
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '_fixture':
        raise SystemExit(worker_main(sys.argv[2], sys.argv[3]))
    raise SystemExit('fixture-only; import run_probe() for offline tests')
