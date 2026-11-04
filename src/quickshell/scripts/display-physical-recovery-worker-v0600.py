#!/usr/bin/env python3
"""Physical-backend transaction rehearsal. No real Hyprland writes or authorization.

Uses the existing IPC lock and transaction.json. The injected transport is a
laboratory-only object; no production transport is accepted in this version.
"""
import importlib.util
from pathlib import Path
import hashlib
import json
import time

HERE = Path(__file__).resolve().parent
P = HERE / 'display-ipc-transaction-lab-v0600.py'
if P.is_symlink() or not P.is_file():
    raise RuntimeError('unsafe IPC dependency')
spec = importlib.util.spec_from_file_location('physical_worker_ipc', P)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)

class Denied(RuntimeError): pass

FIELDS = ('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')

def sha(obj):
    return hashlib.sha256(ipc.canonical(obj)).hexdigest()

def result(decision, record=None):
    return dict(version='0.6.0', decision=decision,
                state=record.get('state') if record else None,
                phase=record.get('phase') if record else None,
                physicalExecutionEnabled=False, executionAuthorized=False,
                commandsExecuted=0, rollbackPhysicalVerified=False)

def _snapshot(path, r):
    s=ipc.read(path,'physical-snapshot.json')
    if not isinstance(s,dict) or s.get('transaction_id')!=r.get('id') or s.get('boot_id')!=r.get('boot_id') or s.get('target')!=r.get('target') or s.get('topology')!=r.get('topology'):
        raise Denied('snapshot identity mismatch')
    if s.get('snapshot_sha256') != sha(s.get('snapshot')) or not isinstance(s.get('snapshot'),list):
        raise Denied('snapshot digest mismatch')
    if len(s['snapshot']) not in (1,2) or set(m.get('name') for m in s['snapshot']) != ({r['target']} if len(s['snapshot'])==1 else {r['target'],'HDMI-A-1'}):
        raise Denied('snapshot topology mismatch')
    if not isinstance(s.get('identity'),dict) or not s['identity'].get('instance'):
        raise Denied('missing compositor identity')
    return s

def _receipt(path,r):
    w=ipc.read(path,'physical-watchdog-rehearsal.json')
    keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
    expected=sha({k:r[k] for k in keys})
    if not isinstance(w,dict) or w.get('transaction_id')!=r.get('id') or w.get('transaction_digest')!=expected or w.get('status')!='ARMED_LAB_ONLY':
        raise Denied('watchdog rehearsal receipt mismatch')

def _normalize(monitors,s,target):
    if not isinstance(monitors,list) or len(monitors)!=len(s['snapshot']):
        raise Denied('hotplug')
    a={m.get('name'):m for m in monitors if isinstance(m,dict)}
    b={m['name']:m for m in s['snapshot']}
    if len(a)!=len(monitors) or set(a)!=set(b):
        raise Denied('connector drift')
    for name in a:
        if any(a[name].get(k)!=b[name][k] for k in FIELDS if k!='refreshRate'):
            raise Denied('geometry or panel drift')
        if name!=target and abs(a[name]['refreshRate']-b[name]['refreshRate'])>0.02:
            raise Denied('external monitor drift')
    hz=a[target].get('refreshRate')
    if type(hz) not in (int,float): raise Denied('invalid refresh')
    if abs(hz-b[target]['refreshRate'])<=0.02: return 'ORIGINAL'
    if abs(hz-144.06)<=0.02: return 'CANDIDATE'
    raise Denied('unknown refresh')

class LabTransport:
    """Explicit in-memory fake. Cannot invoke external commands."""
    def __init__(self, monitors, instance, fail=False):
        self.monitors=json.loads(json.dumps(monitors))
        self.instance=instance
        self.fail=fail
        self.calls=0
    def observe(self):
        return json.loads(json.dumps(self.monitors)),self.instance
    def restore(self,target,original_hz):
        self.calls+=1
        if not self.fail:
            for m in self.monitors:
                if m['name']==target: m['refreshRate']=original_hz

def arm_lab(root):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
        if r.get('execution_backend')!='physical' or r.get('state')!='ARMED' or r.get('phase')!='NONE':
            raise Denied('not a physical armed transaction')
        _snapshot(p,r)
        if time.clock_gettime_ns(time.CLOCK_BOOTTIME)>=r['deadline_boottime_ns']:
            raise Denied('expired')
        if (p/'physical-watchdog-rehearsal.json').exists(): raise Denied('already armed')
        keys=('id','target','topology','plan_sha256','boot_id','deadline_boottime_ns')
        ipc.write(p,'physical-watchdog-rehearsal.json',{'transaction_id':r['id'], 'transaction_digest':sha({k:r[k] for k in keys}), 'status':'ARMED_LAB_ONLY'})
        return result('LAB_WATCHDOG_RECEIPT_ARMED',r)

def recover_lab(root,transport, failpoint=None, now_ns=None):
    if type(transport) is not LabTransport: raise Denied('only lab transport permitted')
    if failpoint not in (None,'after-intent','after-effect'): raise Denied('invalid failpoint')
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
        if r.get('execution_backend')!='physical': raise Denied('not physical')
        if r.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip(): raise Denied('new boot')
        s=_snapshot(p,r)
        _receipt(p,r)
        monitors,instance=transport.observe()
        if instance!=s['identity']['instance']: raise Denied('compositor drift')
        observed=_normalize(monitors,s,r['target'])
        if r['state']=='ROLLED_BACK':
            if r['phase']!='COMPLETED' or observed!='ORIGINAL': raise Denied('inconsistent terminal')
            return result('LAB_TERMINAL_VERIFIED',r)
        if r['state']=='CONFIRMED': return result('CONFIRMED_NO_ACTION',r)
        now=time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_ns is None else now_ns
        if r['state']=='APPLIED' and r['phase']=='APPLY_VERIFIED':
            if now<r['deadline_boottime_ns']: return result('WAITING',r)
            r=dict(r,state='ROLLBACK_REQUIRED',generation=r['generation']+1)
            ipc.write(p,'transaction.json',r)
        if r['state']=='ROLLBACK_REQUIRED' and r['phase']=='APPLY_VERIFIED':
            r=dict(r,state='UNCERTAIN',phase='ROLLBACK_INTENT',generation=r['generation']+1)
            ipc.write(p,'transaction.json',r)
            if failpoint=='after-intent': return result('LAB_INTERRUPTED_AFTER_INTENT',r)
        elif r['state']=='UNCERTAIN' and r['phase']=='ROLLBACK_INTENT':
            pass
        else:
            raise Denied('unsafe phase')
        if observed=='CANDIDATE':
            original=next(m['refreshRate'] for m in s['snapshot'] if m['name']==r['target'])
            transport.restore(r['target'],original)
            if failpoint=='after-effect': return result('LAB_INTERRUPTED_AFTER_EFFECT',r)
        monitors,instance=transport.observe()
        if instance!=s['identity']['instance'] or _normalize(monitors,s,r['target'])!='ORIGINAL':
            return result('LAB_RECOVERY_NOT_VERIFIED',r)
        r=dict(r,state='ROLLED_BACK',phase='COMPLETED',generation=r['generation']+1)
        ipc.write(p,'transaction.json',r)
        return result('LAB_ROLLBACK_VERIFIED_NOT_PHYSICAL',r)

if __name__=='__main__':
    print(json.dumps(result('PHYSICAL_EXECUTION_DISABLED'),indent=2))
