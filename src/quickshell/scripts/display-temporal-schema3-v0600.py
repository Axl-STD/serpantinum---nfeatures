#!/usr/bin/env python3
"""Offline temporal schema-3 sidecar. No physical execution, no systemd mutation.

Does not authorize schema-1 confirmation or replace the existing deadline worker.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

HERE=Path(__file__).resolve().parent
class TemporalDenied(RuntimeError): pass

def controller():
    path=HERE/'display-physical-recovery-controller-v0600.py'
    if path.is_symlink() or not path.is_file(): raise TemporalDenied('unsafe dependency')
    spec=importlib.util.spec_from_file_location('schema3_controller',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def boot_id():
    value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',value):
        raise TemporalDenied('invalid boot identity')
    return value

def clocks():
    return {'realtime_ns':time.time_ns(),'boottime_ns':time.clock_gettime_ns(time.CLOCK_BOOTTIME)}

def _read(root):
    path=root/'temporal-schema3.json'
    try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    except FileNotFoundError:return None
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1 or st.st_size>4096:
            raise TemporalDenied('unsafe sidecar')
        raw=os.read(fd,4097)
        try: value=json.loads(raw)
        except (ValueError,UnicodeError) as exc:raise TemporalDenied('corrupt sidecar') from exc
        required={'clock_schema','boot_id','transaction_id','generation_at_binding','deadline_boottime_ns','deadline_realtime_ns','recovery_required'}
        if not isinstance(value,dict) or set(value)!=required or value['clock_schema']!=3 or type(value['generation_at_binding']) is not int or value['generation_at_binding']<1 or type(value['deadline_boottime_ns']) is not int or value['deadline_boottime_ns']<=0 or type(value['deadline_realtime_ns']) is not int or value['deadline_realtime_ns']<=0 or type(value['recovery_required']) is not bool or not isinstance(value['boot_id'],str) or not isinstance(value['transaction_id'],str):
            raise TemporalDenied('invalid sidecar')
        return value
    finally:os.close(fd)

def bind(root,now=None,current_boot=None):
    c=controller()
    with c._locked(root) as path:
        record=c._read(path)
        if record is None or record['state'] not in ('PREPARED','ARMED','APPLIED'):
            raise TemporalDenied('no eligible transaction')
        if _read(path) is not None:raise TemporalDenied('already bound')
        sample=clocks() if now is None else now
        if set(sample)!={'realtime_ns','boottime_ns'} or any(type(v) is not int or v<=0 for v in sample.values()):
            raise TemporalDenied('invalid clock sample')
        remaining=record['deadline_ns']-sample['realtime_ns']
        if not 0<remaining<=600_000_000_000:raise TemporalDenied('unsafe remaining time')
        value=dict(clock_schema=3,boot_id=boot_id() if current_boot is None else current_boot,
                   transaction_id=record['id'],generation_at_binding=record['generation'],
                   deadline_boottime_ns=sample['boottime_ns']+remaining,
                   deadline_realtime_ns=record['deadline_ns'],recovery_required=True)
        if not re.fullmatch(r'[0-9a-f-]{36}',value['boot_id']):raise TemporalDenied('invalid boot id')
        # Exclusive create: never overwrite an existing sidecar.
        data=(json.dumps(value,sort_keys=True)+'\n').encode()
        fd=os.open(path/'temporal-schema3.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            os.write(fd,data);os.fsync(fd)
        finally:os.close(fd)
        dfd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(dfd)
        finally:os.close(dfd)
        return value

def inspect(root,now=None,current_boot=None):
    c=controller()
    with c._locked(root) as path:
        side=_read(path);record=c._read(path)
        if side is None:return {'decision':'BLOCK_NO_SCHEMA3','executionAuthorized':False}
        if record is None or side['transaction_id']!=record['id'] or side['deadline_realtime_ns']!=record['deadline_ns'] or record['generation']<side['generation_at_binding']:
            decision='BLOCK_IDENTITY_MISMATCH'
        elif side['boot_id']!=(boot_id() if current_boot is None else current_boot):
            decision='RECONCILIATION_REQUIRED_NEW_BOOT'
        elif record['state']=='UNCERTAIN':decision='RECONCILIATION_REQUIRED_UNCERTAIN'
        elif record['state'] in ('CONFIRMED','ROLLED_BACK'):decision='TERMINAL_STATE_OBSERVED'
        else:
            sample=clocks() if now is None else now
            if set(sample)!={'realtime_ns','boottime_ns'}:raise TemporalDenied('invalid sample')
            if type(sample['boottime_ns']) is not int or type(sample['realtime_ns']) is not int:raise TemporalDenied('invalid clock type')
            if sample['boottime_ns']>=side['deadline_boottime_ns']:decision='RECOVERY_DUE'
            elif abs((side['deadline_realtime_ns']-sample['realtime_ns'])-(side['deadline_boottime_ns']-sample['boottime_ns']))>2_000_000_000:
                decision='RECONCILIATION_REQUIRED_CLOCK_DIVERGENCE'
            else:decision='WAITING'
        return {'version':'0.6.0','clock_schema':3,'decision':decision,'transactionState':record['state'] if record else None,
                'recoveryRequired':side['recovery_required'],'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['bind','inspect']);parser.add_argument('root');args=parser.parse_args()
    try:
        result=bind(args.root) if args.action=='bind' else inspect(args.root)
        result.setdefault('physicalExecutionEnabled',False);result.setdefault('commandsExecuted',0)
        result.setdefault('executionAuthorized',False)
        rc=0
    except (OSError,RuntimeError,ValueError,TypeError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'executionAuthorized':False,'physicalExecutionEnabled':False,'commandsExecuted':0};rc=2
    print(json.dumps(result,indent=2));return rc
if __name__=='__main__':sys.exit(main())
