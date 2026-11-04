#!/usr/bin/env python3
"""Physical backend state evaluator. Strictly read-only, no physical authority.

Uses the IPC lock and physical snapshot; NEVER trusts simulated-ipc.json.
No command execution, watchdog arming, or terminal state transition exists here.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
p = HERE / 'display-ipc-transaction-lab-v0600.py'
if not p.is_file() or p.is_symlink():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('physical_backend_ipc', p)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)
FIELDS = ('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')

class Denied(RuntimeError):
    pass

def digest(v):
    return hashlib.sha256(ipc.canonical(v)).hexdigest()

def evaluate(root, observed, *, current_instance=None, current_lua_sha256=None):
    """Evaluate supplied observation. No physical readback is claimed."""
    with ipc.locked(root) as path:
        r = ipc.read(path, 'transaction.json')
        s = ipc.read(path, 'physical-snapshot.json')
        if r.get('execution_backend') != 'physical':
            raise Denied('not physical')
        if r.get('schema') != 1 or r.get('target') not in ('eDP-1','eDP-2'):
            raise Denied('transaction invalid')
        if r.get('state') not in ('APPLIED','ROLLBACK_REQUIRED','UNCERTAIN','ROLLED_BACK'):
            raise Denied('state not recoverable')
        if r.get('phase') not in ('APPLY_VERIFIED','ROLLBACK_INTENT','COMPLETED'):
            raise Denied('phase invalid')
        if not isinstance(s,dict) or s.get('schema') != 1 or s.get('transaction_id') != r.get('id') or s.get('boot_id') != r.get('boot_id') or s.get('target') != r.get('target') or s.get('topology') != r.get('topology'):
            raise Denied('snapshot binding mismatch')
        if type(s.get('generation_at_binding')) is not int or type(r.get('generation')) is not int or s['generation_at_binding'] > r['generation']:
            raise Denied('generation mismatch')
        if not isinstance(s.get('snapshot'),list) or digest(s['snapshot']) != s.get('snapshot_sha256'):
            raise Denied('snapshot digest')
        ident=s.get('identity')
        if not isinstance(ident,dict) or not isinstance(ident.get('instance'),str) or not ident['instance'] or not isinstance(ident.get('lua_sha256'),str) or len(ident['lua_sha256'])!=64:
            raise Denied('identity invalid')
        if current_instance is not None and current_instance != ident['instance']:
            return result('BLOCK_COMPOSITOR_CHANGED',r)
        if current_lua_sha256 is not None and current_lua_sha256 != ident['lua_sha256']:
            return result('BLOCK_LUA_CHANGED',r)
        baseline=s['snapshot']
        if not isinstance(observed,list) or len(observed)!=len(baseline):
            return result('BLOCK_TOPOLOGY_CHANGED',r)
        if any(not isinstance(m,dict) or any(k not in m for k in FIELDS) for m in observed):
            return result('BLOCK_INCOMPLETE_OBSERVATION',r)
        baseline_by_name={m['name']:m for m in baseline}
        observed_by_name={m['name']:m for m in observed}
        if len(baseline_by_name)!=len(baseline) or len(observed_by_name)!=len(observed) or set(baseline_by_name)!=set(observed_by_name):
            return result('BLOCK_TOPOLOGY_CHANGED',r)
        if set(baseline_by_name) not in ({r['target']},{r['target'],'HDMI-A-1'}):
            raise Denied('unsupported snapshot topology')
        for name, base in baseline_by_name.items():
            now=observed_by_name[name]
            if any(now[k]!=base[k] for k in FIELDS if k!='refreshRate'):
                return result('BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',r)
            if name!='HDMI-A-1' and (type(now['refreshRate']) not in (float,int)):
                return result('BLOCK_UNEXPECTED_MODE',r)
            if name=='HDMI-A-1' and now['refreshRate']!=base['refreshRate']:
                return result('BLOCK_PROTECTED_MONITOR_CHANGED',r)
        target=observed_by_name[r['target']]
        base=baseline_by_name[r['target']]
        if abs(target['refreshRate']-base['refreshRate']) <= .10:
            decision='ORIGINAL_OBSERVED_PHYSICAL_UNVERIFIED'
        elif abs(target['refreshRate']-144.06) <= .10:
            decision='CANDIDATE_OBSERVED_RECOVERY_REQUIRED'
        else:
            decision='BLOCK_UNEXPECTED_MODE'
        return result(decision,r)

def result(decision,r):
    return {'version':'0.6.0','decision':decision,'transactionId':r['id'],
            'state':r['state'],'generation':r['generation'],
            'physicalExecutionEnabled':False,'executionAuthorized':False,
            'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--observed',required=True,help='JSON observation file; NOT a live IPC capture')
    ap.add_argument('--instance')
    ap.add_argument('--lua-sha256')
    args=ap.parse_args()
    try:
        obs=json.loads(Path(args.observed).read_text())
        out=evaluate(args.root,obs,current_instance=args.instance,current_lua_sha256=args.lua_sha256)
        rc=0 if out['decision'] in ('ORIGINAL_OBSERVED_PHYSICAL_UNVERIFIED','CANDIDATE_OBSERVED_RECOVERY_REQUIRED') else 2
    except (OSError,RuntimeError,ValueError,TypeError,KeyError) as exc:
        out={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,
             'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}
        rc=2
    print(json.dumps(out,indent=2))
    return rc
if __name__=='__main__':
    sys.exit(main())
