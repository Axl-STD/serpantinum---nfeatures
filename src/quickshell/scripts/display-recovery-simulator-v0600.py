#!/usr/bin/env python3
"""Offline-only simulated display state with serialized apply/confirm/rollback.

Never invokes hyprctl, systemd, or display APIs. No physical recovery claims.
"""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import time

HERE = Path(__file__).resolve().parent
source = HERE / 'display-physical-recovery-controller-v0600.py'
if source.is_symlink() or not source.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('recovery_sim_controller', source)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class SimulationDenied(controller.RecoveryDenied):
    pass

BASE = dict(name='eDP-1',width=1920,height=1080,refresh_mhz=60060,scale_milli=1000,transform=0)
TARGET = dict(BASE,refresh_mhz=144060)


def _observed(root):
    path = root / 'simulated-monitor.json'
    if path.is_symlink():
        raise SimulationDenied('symlink monitor')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1
                or st.st_size > 1024):
            raise SimulationDenied('unsafe simulated monitor')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = json.loads(stream.read(1025))
        if value not in (BASE, TARGET):
            raise SimulationDenied('invalid simulated mode')
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SimulationDenied('corrupt simulated monitor') from exc
    finally:
        os.close(fd)


def _write_observed(root, value):
    # Reuse the controller's durable writer, without reusing recovery.json.
    # Atomic replace and directory fsync are required for simulated evidence.
    import tempfile
    path = root / 'simulated-monitor.json'
    if path.is_symlink():
        raise SimulationDenied('symlink monitor')
    fd, name = tempfile.mkstemp(prefix='.simulated-monitor-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write((json.dumps(value, sort_keys=True) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        os.close(fd)
        if os.path.exists(name):
            os.unlink(name)


def _checked(value, token, generation, expected):
    if value is None or not isinstance(token, str) or not secrets.compare_digest(value['id'], token):
        raise SimulationDenied('foreign transaction')
    if type(generation) is not int or generation != value['generation']:
        raise SimulationDenied('stale generation')
    if value['state'] != expected:
        raise SimulationDenied('invalid phase')


def initialize(root):
    with controller._locked(root) as path:
        if controller._read(path) is not None or _observed(path) is not None:
            raise SimulationDenied('existing state')
        _write_observed(path, BASE)
        return dict(_observed(path))


def apply(root, token, generation, now_ns=None, fail_after_effect=False):
    with controller._locked(root) as path:
        record = controller._read(path)
        _checked(record, token, generation, 'ARMED')
        now = time.time_ns() if now_ns is None else now_ns
        if now >= record['deadline_ns']:
            raise SimulationDenied('deadline expired')
        if record['snapshot'] != BASE or _observed(path) != BASE:
            raise SimulationDenied('snapshot mismatch')
        # Durable intent before simulated effect. If interrupted here, block.
        controller._write(path, dict(record, state='UNCERTAIN', generation=generation+1))
        _write_observed(path, TARGET)
        if fail_after_effect:
            raise SimulationDenied('injected failure after effect: UNCERTAIN')
        result = controller._read(path)
        if _observed(path) != TARGET:
            raise SimulationDenied('verification mismatch')
        result = dict(result, state='APPLIED', generation=result['generation']+1)
        controller._write(path, result)
        return result


def confirm(root, token, generation, now_ns=None):
    with controller._locked(root) as path:
        record = controller._read(path)
        _checked(record, token, generation, 'APPLIED')
        now = time.time_ns() if now_ns is None else now_ns
        if now >= record['deadline_ns'] or _observed(path) != TARGET:
            raise SimulationDenied('expired or mismatched')
        record = dict(record, state='CONFIRMED', generation=generation+1)
        controller._write(path, record)
        return record


def rollback(root, token, generation, fail_after_effect=False):
    with controller._locked(root) as path:
        record = controller._read(path)
        _checked(record, token, generation, 'ROLLBACK_REQUIRED')
        if record['snapshot'] != BASE or _observed(path) not in (BASE, TARGET):
            raise SimulationDenied('snapshot mismatch')
        controller._write(path, dict(record, state='UNCERTAIN', generation=generation+1))
        _write_observed(path, BASE)
        if fail_after_effect:
            raise SimulationDenied('injected failure after restoration: UNCERTAIN')
        if _observed(path) != BASE:
            raise SimulationDenied('restoration mismatch')
        result = controller._read(path)
        result = dict(result, state='ROLLED_BACK', generation=result['generation']+1)
        controller._write(path, result)
        return result


def inspect(root):
    with controller._locked(root) as path:
        return {'recovery': controller._read(path), 'simulatedMonitor': _observed(path),
                'physicalExecutionEnabled': False, 'commandsExecuted': 0,
                'logicalRecoveryVerified': False}
