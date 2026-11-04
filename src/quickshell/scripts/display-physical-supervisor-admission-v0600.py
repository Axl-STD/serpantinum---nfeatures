#!/usr/bin/env python3
"""Fail-closed offline physical supervisor admission; never arms or executes a watchdog."""
import argparse
import hashlib
import json
from pathlib import Path

REQUIRED = ('id','boot_id','target','topology','plan_sha256','deadline_boottime_ns')
FIELDS = ('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')

def digest(value):
    return hashlib.sha256((json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()).hexdigest()

def result(decision, **extra):
    return dict(version='0.6.0', decision=decision, physicalExecutionEnabled=False,
                executionAuthorized=False, watchdogPhysicalArmed=False,
                commandsExecuted=0, rollbackPhysicalVerified=False, **extra)

def evaluate(transaction, snapshot, receipt, instances, monitors, service):
    try:
        if not all(isinstance(x,dict) for x in (transaction,snapshot,receipt,service)):
            return result('BLOCK_SCHEMA')
        r=transaction
        if r.get('execution_backend')!='physical' or r.get('state') not in ('ARMED','APPLIED','ROLLBACK_REQUIRED','UNCERTAIN'):
            return result('BLOCK_TRANSACTION')
        if r.get('target') not in ('eDP-1','eDP-2') or r.get('topology') not in ('internal-only','internal-and-hdmi'):
            return result('BLOCK_TARGET')
        if any(k not in r for k in REQUIRED) or type(r.get('generation')) is not int or r['generation']<0:
            return result('BLOCK_TRANSACTION_SCHEMA')
        if type(r['deadline_boottime_ns']) is not int or r['deadline_boottime_ns']<=0:
            return result('BLOCK_DEADLINE')
        if not isinstance(r['id'],str) or len(r['id'])!=64 or not isinstance(r['plan_sha256'],str) or len(r['plan_sha256'])!=64:
            return result('BLOCK_TRANSACTION_SCHEMA')
        if any(snapshot.get(k)!=r.get(k) for k in ('boot_id','target','topology')) or snapshot.get('transaction_id')!=r['id']:
            return result('BLOCK_SNAPSHOT_IDENTITY')
        saved=snapshot.get('snapshot')
        if not isinstance(saved,list) or len(saved) not in (1,2) or snapshot.get('snapshot_sha256')!=digest(saved):
            return result('BLOCK_SNAPSHOT_DIGEST')
        expected={r['target']} if r['topology']=='internal-only' else {r['target'],'HDMI-A-1'}
        if any(not isinstance(m,dict) or any(k not in m for k in FIELDS) for m in saved) or {m['name'] for m in saved}!=expected or len(saved)!=len(expected):
            return result('BLOCK_SNAPSHOT_TOPOLOGY')
        if receipt.get('status')=='ARMED_LAB_ONLY':
            return result('BLOCK_LAB_RECEIPT')
        # Deliberately no receipt status is accepted as physical authorization.
        if receipt.get('transaction_id')!=r['id'] or receipt.get('transaction_digest')!=digest({k:r[k] for k in REQUIRED}):
            return result('BLOCK_RECEIPT')
        if not isinstance(instances,list) or len(instances)!=1 or not isinstance(instances[0],dict):
            return result('BLOCK_INSTANCE')
        instance=instances[0].get('instance')
        if not isinstance(instance,str) or not instance or snapshot.get('identity',{}).get('instance')!=instance:
            return result('BLOCK_INSTANCE_DRIFT')
        if not isinstance(monitors,list) or len(monitors)!=len(saved) or any(not isinstance(m,dict) for m in monitors):
            return result('BLOCK_TOPOLOGY_DRIFT')
        current={m.get('name'):m for m in monitors}
        if len(current)!=len(monitors) or set(current)!=expected:
            return result('BLOCK_TOPOLOGY_DRIFT')
        original={m['name']:m for m in saved}
        for name in expected:
            if any(current[name].get(k)!=original[name][k] for k in FIELDS if k!='refreshRate'):
                return result('BLOCK_GEOMETRY_DRIFT')
            hz=current[name].get('refreshRate')
            if type(hz) not in (int,float):
                return result('BLOCK_REFRESH')
            if name!=r['target'] and abs(hz-original[name]['refreshRate'])>0.02:
                return result('BLOCK_EXTERNAL_DRIFT')
            if name==r['target'] and min(abs(hz-original[name]['refreshRate']),abs(hz-144.06))>0.02:
                return result('BLOCK_REFRESH')
        if service.get('LoadState')!='loaded' or service.get('ActiveState') not in ('active','activating'):
            return result('BLOCK_SERVICE_NOT_ACTIVE')
        if service.get('Result') not in (None,'success'):
            return result('BLOCK_SERVICE_FAILED')
        return result('READONLY_SUPERVISOR_EVIDENCE_CONSISTENT',target=r['target'],instance=instance,
                      transactionId=r['id'],generation=r['generation'],serviceObserved=True,
                      physicalAuthorizationNotEstablished=True)
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError):
        return result('BLOCK_INVALID_EVIDENCE')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('transaction','snapshot','receipt','instances','monitors','service'):
        p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args()
    try:
        data=[json.loads(getattr(a,n).read_text()) for n in ('transaction','snapshot','receipt','instances','monitors','service')]
        out=evaluate(*data)
    except (OSError,ValueError,TypeError):
        out=result('BLOCK_INPUT')
    print(json.dumps(out,indent=2))
    return 0 if out['decision']=='READONLY_SUPERVISOR_EVIDENCE_CONSISTENT' else 2

if __name__=='__main__':
    raise SystemExit(main())
