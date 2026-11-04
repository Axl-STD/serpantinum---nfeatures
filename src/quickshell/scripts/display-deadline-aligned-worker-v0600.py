#!/usr/bin/env python3
"""Wait for persistent wall-clock deadline, then invoke offline simulated rollback.

Wall clock is not safe for physical execution; no monitor APIs are present.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
HERE=Path(__file__).resolve().parent

def load(name, alias):
    p=HERE/name
    if p.is_symlink() or not p.is_file():raise RuntimeError('unsafe dependency')
    s=importlib.util.spec_from_file_location(alias,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def execute(root, now=time.time_ns, sleep=time.sleep):
    rollback=load('display-systemd-auto-rollback-v0600.py','deadline_rollback')
    c=rollback.c
    root=c._root(Path(root))
    initial=c.inspect(root)
    if not initial:return {'decision':'BLOCK_NO_STATE'}
    if initial['state'] in ('CONFIRMED','ROLLED_BACK'):
        return rollback.execute(root)
    if initial['state'] not in ('APPLIED','ROLLBACK_REQUIRED'):
        return {'decision':'BLOCK_UNSAFE_STATE'}
    token=initial['id']
    deadline=initial['deadline_ns']
    if deadline-now()>600_000_000_000:
        return {'decision':'BLOCK_EXCESSIVE_WAIT'}
    while True:
        state=c.inspect(root)
        if not state or state['id']!=token or state['deadline_ns']!=deadline:
            return {'decision':'BLOCK_IDENTITY_CHANGED'}
        if state['state'] in ('CONFIRMED','ROLLED_BACK'):
            return rollback.execute(root)
        if state['state'] not in ('APPLIED','ROLLBACK_REQUIRED'):
            return {'decision':'BLOCK_UNSAFE_STATE'}
        remaining=deadline-now()
        if remaining<=0:break
        sleep(min(0.25,remaining/1e9))
    return rollback.execute(root)

def main():
    p=argparse.ArgumentParser();p.add_argument('root');a=p.parse_args()
    try:r=execute(a.root)
    except (OSError,ValueError,RuntimeError) as e:r={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(e).__name__}
    r.setdefault('physicalExecutionEnabled',False);r.setdefault('commandsExecuted',0)
    print(json.dumps(r,indent=2));return 0 if r['decision'] in ('SIMULATED_ROLLBACK_VERIFIED','CONFIRMED_NO_ROLLBACK','ALREADY_ROLLED_BACK') else 2
if __name__=='__main__':sys.exit(main())
