#!/usr/bin/env python3
"""Systemd automatic restart E2E for disk-backed simulated monitor only.

No physical monitor commands. The first worker is SIGKILLed after an explicit
lab failpoint, and systemd must restart the SAME unit to finish recovery.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
SHARED = HERE / 'display-physical-shared-transport-v0600.py'


def load():
    if SHARED.is_symlink() or not SHARED.is_file():
        raise RuntimeError('missing or unsafe shared transport')
    spec = importlib.util.spec_from_file_location('shared_systemd_e2e', SHARED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare(w, root):
    ipc = w.ipc
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    base = dict(name='eDP-1', description='AU Optronics', width=1920,
                height=1080, refreshRate=60.061, x=0, y=0, scale=1,
                transform=0, disabled=False, dpmsStatus=True)
    record = dict(schema=1, id=secrets.token_hex(32), generation=4,
                  state='ROLLBACK_REQUIRED', phase='APPLY_VERIFIED',
                  execution_backend='physical', target='eDP-1',
                  topology='internal-only', plan_sha256='b' * 64,
                  boot_id=boot,
                  deadline_boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)-1_000_000_000)
    snapshot = dict(transaction_id=record['id'], boot_id=boot,
                    target='eDP-1', topology='internal-only', snapshot=[base],
                    identity={'instance': 'lab_instance'},
                    snapshot_sha256=w.sha([base]))
    keys = ('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    receipt = dict(transaction_id=record['id'],
                   transaction_digest=w.sha({k: record[k] for k in keys}),
                   status='ARMED_LAB_ONLY')
    with ipc.locked(root) as p:
        ipc.write(p, 'transaction.json', record)
        ipc.write(p, 'physical-snapshot.json', snapshot)
        ipc.write(p, 'physical-watchdog-rehearsal.json', receipt)
        ipc.write(p, 'shared-lab-monitor.json', dict(schema=1,
                  instance='lab_instance', monitors=[dict(base, refreshRate=144.06)]))
    return record


def worker(root, failpoint):
    w = load()
    marker = root / 'first-worker-started'
    # marker is created exclusively. This marker is NOT authority for recovery.
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        first = False
    else:
        first = True
        with os.fdopen(fd, 'w') as f:
            f.write(f'{os.getpid()}\n')
            f.flush()
            os.fsync(f.fileno())
    if first:
        result = w.recover_lab(root, w.SharedLabTransport(root), failpoint=failpoint)
        expected = ('LAB_INTERRUPTED_AFTER_INTENT' if failpoint == 'after-intent'
                    else 'LAB_INTERRUPTED_AFTER_EFFECT')
        if result['decision'] != expected:
            print(json.dumps(result), flush=True)
            return 2
        print('LAB_FIRST_WORKER_SIGKILL '+failpoint, flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
        return 3
    result = w.recover_lab(root, w.SharedLabTransport(root))
    print(json.dumps(result), flush=True)
    return 0 if result['decision'] == 'LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL' else 2


def run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def case(w, base, failpoint):
    root = base / failpoint
    root.mkdir(mode=0o700)
    initial = prepare(w, root)
    unit = 'serpantinum-v0600-sharedauto-' + secrets.token_hex(6)
    command = ['systemd-run','--user','--unit='+unit,
               '--property=Type=exec','--property=Restart=on-failure',
               '--property=RestartSec=1s','--property=StartLimitBurst=3',
               '--property=StartLimitIntervalSec=60s',
               '--property=TimeoutStartSec=45s',
               sys.executable,'-B',str(Path(__file__).resolve()),
               'worker',str(root),failpoint]
    scheduled = run(command)
    if scheduled.returncode:
        raise RuntimeError('systemd-run failed: '+scheduled.stderr)
    deadline = time.monotonic()+40
    last = None
    while time.monotonic() < deadline:
        with w.ipc.locked(root) as p:
            record = w.ipc.read(p, 'transaction.json')
            monitor = w.ipc.read(p, 'shared-lab-monitor.json')
        state = (record.get('state'),record.get('phase'),monitor['monitors'][0]['refreshRate'])
        last = state
        if state == ('ROLLED_BACK','COMPLETED',60.061):
            break
        time.sleep(.2)
    else:
        raise AssertionError('recovery timeout: '+repr(last))
    # Journal evidence is more reliable than NRestarts after a transient unit exits.
    journal = run(['journalctl','--user','-u',unit+'.service','--no-pager','-o','cat'], 15)
    output = journal.stdout
    (base/(failpoint+'-journal.txt')).write_text(output)
    assert journal.returncode == 0
    assert 'LAB_FIRST_WORKER_SIGKILL '+failpoint in output
    assert 'status=9/KILL' in output
    assert 'restart counter is at 1' in output
    assert 'LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL' in output
    status = run(['systemctl','--user','show',unit+'.service','-p','Result','-p','ExecMainStatus'])
    assert status.returncode == 0 and 'Result=success' in status.stdout and 'ExecMainStatus=0' in status.stdout, status.stdout
    assert record['id'] == initial['id'] and record['generation'] == 6
    # A fresh third process must see the terminal state, not replay the effect.
    terminal = run([sys.executable,'-B',str(SHARED),'recover',str(root)])
    assert terminal.returncode == 0 and json.loads(terminal.stdout)['decision'] == 'LAB_TERMINAL_VERIFIED'
    return dict(scenario=failpoint, passed=True, unit=unit,
                state=record['state'], phase=record['phase'], generation=record['generation'])


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('run')
    worker_parser=sub.add_parser('worker')
    worker_parser.add_argument('root')
    worker_parser.add_argument('failpoint',choices=['after-intent','after-effect'])
    a=p.parse_args()
    if a.action == 'worker':
        return worker(Path(a.root),a.failpoint)
    w=load()
    with tempfile.TemporaryDirectory(prefix='serpantinum-shared-auto-',dir='/tmp') as temp:
        base=Path(temp)
        base.chmod(0o700)
        results=[]
        try:
            for scenario in ('after-intent','after-effect'):
                results.append(case(w,base,scenario))
        except Exception as exc:
            # Preserve evidence on failure without deleting the lab transaction.
            keep=Path('/tmp')/('serpantinum-shared-auto-failure-'+secrets.token_hex(4))
            import shutil
            shutil.copytree(base,keep)
            print(json.dumps(dict(selftest='failed',error=repr(exc),evidence=str(keep),results=results,
                                  physicalExecutionEnabled=False,commandsExecuted=0),indent=2))
            return 2
    print(json.dumps(dict(selftest='passed',totalScenarios=2,results=results,
                          physicalExecutionEnabled=False,commandsExecuted=0,
                          rollbackPhysicalVerified=False),indent=2))
    return 0

if __name__=='__main__':
    sys.exit(main())
