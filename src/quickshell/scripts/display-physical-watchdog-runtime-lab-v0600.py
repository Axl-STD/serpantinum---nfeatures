#!/usr/bin/env python3
"""Systemd-ready LAB-only watchdog for persistent fake monitor. NEVER physical."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
DEP = HERE / 'display-physical-shared-transport-v0600.py'
if DEP.is_symlink() or not DEP.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('serpantinum_shared_lab', DEP)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
ipc = shared.ipc
PREFIX = 'serpantinum-v0600-pwlab-'


def result(decision, detail=None):
    return dict(version='0.6.0', mode='physical-watchdog-lab-only', decision=decision,
                detail=detail, physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)


def validate(root):
    root = ipc.root_check(Path(root))
    with ipc.locked(root) as p:
        r = ipc.read(p, 'transaction.json')
        if r.get('execution_backend') != 'physical':
            raise shared.Denied('backend mismatch')
        if r.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            raise shared.Denied('boot mismatch')
        snap = shared._snapshot(p, r)
        shared._receipt(p, r)  # ONLY ARMED_LAB_ONLY
        if not (p / 'shared-lab-monitor.json').is_file():
            raise shared.Denied('shared lab state absent')
        monitors, instance = shared.SharedLabTransport(p).observe()
        if instance != snap['identity']['instance']:
            raise shared.Denied('compositor identity mismatch')
        shared._normalize(monitors, snap, r['target'])
        return r


def run(root, max_wait=90, poll=0.1, sleeper=time.sleep):
    if type(max_wait) not in (int, float) or not 0 <= max_wait <= 120:
        raise ValueError('max_wait')
    if type(poll) not in (int, float) or not 0.02 <= poll <= 2:
        raise ValueError('poll')
    initial = validate(root)
    token = initial['id']
    end = time.monotonic() + max_wait
    while True:
        current = validate(root)
        if current['id'] != token:
            return result('BLOCK_IDENTITY_CHANGED')
        if current['state'] == 'ARMED':
            return result('LAB_ARMED_NO_EFFECT')
        if current['state'] == 'CONFIRMED':
            return result('CONFIRMED_NO_ACTION')
        if current['state'] == 'APPLIED' and time.clock_gettime_ns(time.CLOCK_BOOTTIME) < current['deadline_boottime_ns']:
            remaining = min(end-time.monotonic(), (current['deadline_boottime_ns']-time.clock_gettime_ns(time.CLOCK_BOOTTIME))/1e9)
            if remaining <= 0:
                return result('WAITING')
            sleeper(min(poll, remaining))
            continue
        response = shared.recover_lab(root, shared.SharedLabTransport(root))
        decision = response['decision']
        if decision in ('LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL', 'LAB_TERMINAL_VERIFIED'):
            final = validate(root)
            if final['id'] != token or final['state'] != 'ROLLED_BACK' or final['phase'] != 'COMPLETED':
                return result('BLOCK_FINAL_STATE')
            return result('LAB_RECOVERY_VERIFIED_NOT_PHYSICAL', decision)
        if decision == 'WAITING':
            if time.monotonic() >= end:
                return result('WAITING')
            sleeper(poll)
            continue
        return result('BLOCK_AND_INVESTIGATE', decision)


def schedule(root, unit, delay=1, runner=subprocess.run):
    if not isinstance(unit, str) or not re.fullmatch(PREFIX + r'[A-Za-z0-9]{8,24}', unit):
        raise ValueError('unit')
    if type(delay) is not int or not 1 <= delay <= 30:
        raise ValueError('delay')
    before = validate(root)
    if before['state'] != 'ARMED' or before['phase'] != 'NONE':
        raise ValueError('not armable')
    if time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= before['deadline_boottime_ns']:
        raise ValueError('expired')
    root = ipc.root_check(Path(root))
    cmd = ['systemd-run','--user','--unit='+unit,'--on-active='+str(delay)+'s',
           '--timer-property=AccuracySec=100ms','--property=Type=oneshot',
           '--property=TimeoutStartSec=120s',sys.executable,'-B',str(Path(__file__).resolve()),
           'run',str(root),'--max-wait','90']
    p = runner(cmd,capture_output=True,text=True,timeout=15)
    if p.returncode:
        return result('BLOCK_SCHEDULE_FAILED')
    q = runner(['systemctl','--user','show',unit+'.timer','-p','LoadState','-p','ActiveState','--no-pager'],
               capture_output=True,text=True,timeout=15)
    if q.returncode or 'LoadState=loaded' not in q.stdout or 'ActiveState=active' not in q.stdout:
        return result('BLOCK_TIMER_UNVERIFIED')
    after = validate(root)
    if (after['id'],after['generation'],after['state']) != (before['id'],before['generation'],'ARMED'):
        return result('BLOCK_STATE_CHANGED')
    return result('LAB_TIMER_VERIFIED_NOT_PHYSICAL',unit)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    for action in ('inspect','run','schedule'):
        a=sub.add_parser(action);a.add_argument('root')
        if action=='run': a.add_argument('--max-wait',type=float,default=90)
        if action=='schedule':
            a.add_argument('--unit',required=True);a.add_argument('--delay',type=int,default=1)
    args=p.parse_args()
    try:
        if args.action=='inspect':
            r=validate(args.root)
            output=result('LAB_READONLY_VALID',dict(state=r['state'],generation=r['generation']))
        elif args.action=='run': output=run(args.root,args.max_wait)
        else: output=schedule(args.root,args.unit,args.delay)
    except (OSError,RuntimeError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
        output=result('BLOCK_AND_INVESTIGATE',type(exc).__name__)
    print(json.dumps(output,indent=2))
    return 0 if output['decision'] in ('LAB_READONLY_VALID','LAB_ARMED_NO_EFFECT','CONFIRMED_NO_ACTION','LAB_RECOVERY_VERIFIED_NOT_PHYSICAL','LAB_TIMER_VERIFIED_NOT_PHYSICAL') else 2

if __name__=='__main__':
    sys.exit(main())
