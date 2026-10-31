#!/usr/bin/env python3
"""Serpantinum v0.5.3: systemd user transient watchdog, simulation ONLY.

No monitor mutations. No persistent systemd unit. Uses private runtime state,
unique transient unit names, and explicit readiness verification.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

PREFIX = 'serpantinum-display-v053-'

def runtime_dir():
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base:
        raise RuntimeError('XDG_RUNTIME_DIR is required')
    parent = Path(base)
    if not parent.is_dir() or parent.is_symlink() or parent.stat().st_uid != os.getuid():
        raise RuntimeError('Unsafe runtime directory')
    path = parent / 'serpantinum-display-v053'
    if path.is_symlink():
        raise RuntimeError('State path is a symlink')
    path.mkdir(mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe state permissions')
    return path

@contextlib.contextmanager
def lock(directory):
    fd = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield

def load(directory):
    path = directory / 'state.json'
    if path.is_symlink():
        raise RuntimeError('State file is a symlink')
    return json.loads(path.read_text()) if path.exists() else {'phase':'idle','pending':False}

def save(directory, data):
    fd, name = tempfile.mkstemp(dir=directory, prefix='.state-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / 'state.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)

def run(*args, timeout=8):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=timeout)

def unit_status(unit):
    result = subprocess.run(['systemctl','--user','show',unit,'--property=ActiveState','--property=SubState','--no-pager'], text=True, capture_output=True, timeout=6)
    if result.returncode:
        return 'unknown'
    return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line).get('ActiveState','unknown')

def start(directory, seconds):
    if not 5 <= seconds <= 120:
        raise ValueError('Timeout must be 5-120 seconds')
    with lock(directory):
        previous = load(directory)
        if previous.get('pending'):
            raise ValueError('Transaction already pending; inspect status')
        token = secrets.token_hex(12)
        unit = PREFIX + token + '.service'
        now = time.time()
        state = dict(schemaVersion=1, phase='starting', pending=True, id=token, unit=unit,
                     deadline=now+seconds, timeoutSeconds=seconds, watchdogReady=False,
                     physicalOutputsChanged=False, persistentConfigChanged=False)
        save(directory,state)
    try:
        run('systemd-run','--user','--unit='+unit.removesuffix('.service'),'--collect','--quiet',
            sys.executable, str(Path(__file__).resolve()), '_watch', '--id', token)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            with lock(directory):
                current = load(directory)
                if current.get('id') == token and current.get('watchdogReady') and unit_status(unit) == 'active':
                    return current
            time.sleep(.1)
        raise RuntimeError('Watchdog readiness not confirmed')
    except Exception:
        with lock(directory):
            current = load(directory)
            if current.get('id') == token and current.get('pending'):
                current.update(phase='startup-failed',pending=False,watchdogReady=False)
                save(directory,current)
        subprocess.run(['systemctl','--user','stop',unit],capture_output=True,timeout=5)
        raise

def watch(directory, token):
    with lock(directory):
        state = load(directory)
        if state.get('id') != token or not state.get('pending'):
            return
        state.update(watchdogReady=True, phase='simulated-pending')
        save(directory,state)
    while True:
        with lock(directory):
            state = load(directory)
            if state.get('id') != token or not state.get('pending'):
                return
            remaining = state['deadline']-time.time()
            if remaining <= 0:
                state.update(phase='simulated-rollback-timeout',pending=False,finishedAt=time.time())
                save(directory,state)
                return
        time.sleep(min(.25, max(.01,remaining)))

def resolve(directory, token, operation):
    with lock(directory):
        state=load(directory)
        if not state.get('pending') or state.get('id') != token or not state.get('watchdogReady'):
            raise ValueError('No matching ready transaction')
        if time.time() >= state['deadline']:
            raise ValueError('Deadline expired; wait for watchdog')
        state.update(phase='simulated-confirmed' if operation=='confirm' else 'simulated-rollback-manual',pending=False,finishedAt=time.time())
        save(directory,state)
        return state

def selftest():
    with tempfile.TemporaryDirectory(prefix='serpantinum-v053-offline-') as temp:
        directory=Path(temp)
        state=dict(id='test',pending=True,deadline=time.time()-.01,watchdogReady=False,phase='starting')
        save(directory,state)
        watch(directory,'test')
        result=load(directory)
        assert result['phase']=='simulated-rollback-timeout' and not result['pending']
        assert not result.get('physicalOutputsChanged',False)
        return dict(offline='passed',systemdIntegration='not-run',physicalOutputsChanged=False)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['selftest','start','status','confirm','rollback','_watch'])
    p.add_argument('--seconds',type=int,default=15)
    p.add_argument('--id')
    args=p.parse_args()
    if args.action=='selftest':
        print(json.dumps(selftest(),indent=2));return
    directory=runtime_dir()
    if args.action=='start':
        result=start(directory,args.seconds)
    elif args.action=='_watch':
        if not args.id: p.error('--id required')
        watch(directory,args.id)
        return
    elif args.action=='status':
        with lock(directory): result=load(directory)
        if result.get('unit'):
            result=dict(result,systemdActiveState=unit_status(result['unit']))
    else:
        if not args.id: p.error('--id required')
        result=resolve(directory,args.id,args.action)
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    try: main()
    except (RuntimeError,ValueError,OSError,subprocess.SubprocessError,AssertionError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr);sys.exit(1)
