#!/usr/bin/env python3
"""Read-only recovery evidence decision. Never authorizes or performs display changes."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
P=HERE/'display-physical-snapshot-binding-v0600.py'
if not P.is_file() or P.is_symlink():
    raise RuntimeError('unsafe dependency')
spec=importlib.util.spec_from_file_location('evidence_snapshot',P)
snap=importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)
ipc=snap.ipc

class Denied(RuntimeError): pass

def decide(root, monitors, capture):
    """Compare physical readback to stored baseline without changing transaction state."""
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
        s=ipc.read(p,'physical-snapshot.json')
        if not isinstance(r,dict) or not isinstance(s,dict): raise Denied('invalid records')
        if set(s)!={'schema','transaction_id','generation_at_binding','boot_id','target','topology','snapshot','snapshot_sha256','identity'} or s['schema']!=1: raise Denied('invalid sidecar')
        if (r.get('id')!=s['transaction_id'] or r.get('boot_id')!=s['boot_id'] or
            r.get('target')!=s['target'] or r.get('topology')!=s['topology'] or
            type(r.get('generation')) is not int or r['generation']<s['generation_at_binding'] or
            snap.digest(s['snapshot'])!=s['snapshot_sha256']): raise Denied('transaction drift')
        baseline=snap.validate(s['snapshot'],s['target'])
        if baseline!=s['snapshot']: raise Denied('baseline malformed')
        if snap.identity(capture,s['target'],s['topology'])!=s['identity']: raise Denied('capture identity changed')
        if not isinstance(monitors,list) or len(monitors)!=len(baseline): raise Denied('topology changed')
        observed={}
        for item in monitors:
            if not isinstance(item,dict) or not all(k in item for k in snap.FIELDS): raise Denied('missing observation')
            name=item['name']
            if name in observed or name not in {m['name'] for m in baseline}: raise Denied('connector changed')
            observed[name]={k:item[k] for k in snap.FIELDS}
        if set(observed)!={m['name'] for m in baseline}: raise Denied('connector changed')
        for old in baseline:
            now=observed[old['name']]
            for key in snap.FIELDS:
                if key=='refreshRate' and old['name']==s['target']: continue
                if type(now[key]) is not type(old[key]) or now[key]!=old[key]: raise Denied('geometry, power or external monitor drift')
        hz=observed[s['target']]['refreshRate']
        if type(hz) not in (int,float): raise Denied('invalid refresh')
        if 59.95<=hz<=60.15: mode='ORIGINAL_OBSERVED'
        elif 143.95<=hz<=144.15: mode='CANDIDATE_OBSERVED'
        else: raise Denied('unexpected mode')
        state=r.get('state'); phase=r.get('phase')
        if state=='ROLLED_BACK' and phase=='COMPLETED' and mode=='ORIGINAL_OBSERVED': decision='PHYSICAL_ORIGINAL_OBSERVED_TERMINAL_UNVERIFIED'
        elif state in ('APPLIED','ROLLBACK_REQUIRED','UNCERTAIN'):
            decision='PHYSICAL_ORIGINAL_OBSERVED_PENDING' if mode=='ORIGINAL_OBSERVED' else 'PHYSICAL_CANDIDATE_OBSERVED_PENDING'
        elif state in ('PREPARED','ARMED') and mode=='ORIGINAL_OBSERVED': decision='PHYSICAL_BASELINE_OBSERVED'
        else: raise Denied('state/effect mismatch')
        return {'version':'0.6.0','decision':decision,'state':state,'phase':phase,'target':s['target'],'observedRefreshRate':hz,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--monitors',required=True);p.add_argument('--capture',required=True);a=p.parse_args()
    try: out=decide(Path(a.root),json.loads(Path(a.monitors).read_text()),json.loads(Path(a.capture).read_text()));rc=0
    except (OSError,RuntimeError,ValueError,TypeError,KeyError) as e:
        out={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(e).__name__,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False};rc=2
    print(json.dumps(out,indent=2));return rc
if __name__=='__main__': raise SystemExit(main())
