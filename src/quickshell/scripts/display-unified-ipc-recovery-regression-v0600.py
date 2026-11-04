#!/usr/bin/env python3
"""Unified IPC recovery regression, fully offline."""
import importlib.util
import json
import multiprocessing
from pathlib import Path
import secrets
import tempfile
import time

HERE=Path(__file__).resolve().parent

def load(name,alias):
    p=HERE/name
    s=importlib.util.spec_from_file_location(alias,p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    return m

ipc=load('display-ipc-transaction-lab-v0600.py','test_ipc')
worker=load('display-unified-ipc-recovery-v0600.py','test_worker')
BOOT=Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def plan(connector='eDP-1',topology='internal-only'):
    inst='test-instance'
    return {'decision':'DYNAMIC_TOPOLOGY_COMMANDS_READONLY','target':connector,
            'protected':'HDMI-A-1','topology':topology,'physicalExecutionEnabled':False,
            'executionAuthorized':False,'commandsExecuted':0,
            'candidateArgs':['hyprctl','-i',inst,'keyword','monitor',f'{connector},{ipc.TARGET},0x0,1'],
            'rollbackArgs':['hyprctl','-i',inst,'keyword','monitor',f'{connector},{ipc.BASE},0x0,1']}


def scenario(connector='eDP-1',topology='internal-only',deadline_offset=3_000_000_000):
    tmp=tempfile.TemporaryDirectory(prefix='unified-ipc-')
    root=Path(tmp.name)/'transaction';root.mkdir(mode=0o700)
    record=ipc.prepare(root,plan(connector,topology),time.clock_gettime_ns(time.CLOCK_BOOTTIME)+deadline_offset,BOOT)
    ipc.action(root,record['id'],'arm',boot_id=BOOT)
    return tmp,root,record['id']


def checked(results,label,condition):
    if not condition:raise AssertionError(label)
    results.append({'scenario':label,'passed':True})


def race_process(root):
    worker.execute(root,now_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+100_000_000_000,boot_id=BOOT)


def main():
    results=[]
    for connector,topology in [('eDP-1','internal-only'),('eDP-2','internal-only'),('eDP-1','internal-and-hdmi'),('eDP-2','internal-and-hdmi')]:
        tmp,root,token=scenario(connector,topology)
        try:
            r=ipc.action(root,token,'apply',boot_id=BOOT)
            checked(results,f'{connector}-{topology}-applied',r['state']=='APPLIED')
            checked(results,f'{connector}-{topology}-waiting',worker.execute(root,boot_id=BOOT)['decision']=='WAITING')
            checked(results,f'{connector}-{topology}-newboot',worker.execute(root,boot_id='foreign')['decision']=='BLOCK_NEW_BOOT')
            due=worker.execute(root,now_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME)+100_000_000_000,boot_id=BOOT)
            checked(results,f'{connector}-{topology}-rollback',due['decision']=='SIMULATED_ROLLBACK_VERIFIED')
            checked(results,f'{connector}-{topology}-terminal',worker.execute(root,boot_id=BOOT)['decision']=='TERMINAL_VERIFIED')
            checked(results,f'{connector}-{topology}-mode',ipc.action(root,token,'inspect')['observed']['mode']==ipc.BASE)
        finally:tmp.cleanup()
    for failpoint,expected in [('after-intent','BLOCK_UNCERTAIN_EFFECT'),('after-effect','SIMULATED_RECONCILIATION_VERIFIED')]:
        tmp,root,token=scenario()
        try:
            ipc.action(root,token,'apply',boot_id=BOOT)
            ipc.action(root,token,'rollback',failpoint=failpoint,boot_id=BOOT)
            out=worker.execute(root,boot_id=BOOT)
            checked(results,'rollback-'+failpoint,out['decision']==expected)
            checked(results,'rollback-'+failpoint+'-idempotent',worker.execute(root,boot_id=BOOT)['decision']==('TERMINAL_VERIFIED' if failpoint=='after-effect' else expected))
        finally:tmp.cleanup()
    for failpoint in ('after-intent','after-effect'):
        tmp,root,token=scenario()
        try:
            ipc.action(root,token,'apply',failpoint=failpoint,boot_id=BOOT)
            checked(results,'apply-'+failpoint,worker.execute(root,boot_id=BOOT)['decision']=='BLOCK_UNCERTAIN_APPLY')
        finally:tmp.cleanup()
    tmp,root,token=scenario()
    try:
        ipc.action(root,token,'apply',boot_id=BOOT)
        processes=[multiprocessing.Process(target=race_process,args=(root,)) for _ in range(4)]
        for p in processes:p.start()
        for p in processes:p.join(timeout=8)
        checked(results,'concurrent-exits',all(p.exitcode==0 for p in processes))
        checked(results,'concurrent-terminal',worker.execute(root,boot_id=BOOT)['decision']=='TERMINAL_VERIFIED')
        checked(results,'concurrent-generation',ipc.action(root,token,'inspect')['record']['generation']==7)
    finally:tmp.cleanup()
    tmp,root,token=scenario()
    try:
        ipc.action(root,token,'apply',boot_id=BOOT)
        with ipc.locked(root) as p:ipc.write(p,'simulated-ipc.json',{'connector':'eDP-2','mode':ipc.TARGET})
        checked(results,'connector-drift',worker.execute(root,boot_id=BOOT)['decision']=='BLOCK_INVALID_STATE')
    finally:tmp.cleanup()
    tmp,root,token=scenario()
    try:
        ipc.action(root,token,'apply',boot_id=BOOT)
        with ipc.locked(root) as p:ipc.write(p,'transaction.json',{'broken':True})
        checked(results,'corrupt-record',worker.execute(root,boot_id=BOOT)['decision']=='BLOCK_INVALID_STATE')
    finally:tmp.cleanup()
    out={'selftest':'passed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0}
    print(json.dumps(out,indent=2))

if __name__=='__main__':main()
