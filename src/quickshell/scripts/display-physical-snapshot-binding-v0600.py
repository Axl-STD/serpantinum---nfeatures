#!/usr/bin/env python3
"""Fail-closed, read-only physical snapshot sidecar for simulated IPC transactions.

No Hyprland mutation, no physical authorization. The sidecar is protected by the
existing IPC transaction lock. Snapshot is evidence, not a recovery capability.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
P = HERE / 'display-ipc-transaction-lab-v0600.py'
if P.is_symlink() or not P.is_file():
    raise RuntimeError('unsafe dependency')
spec = importlib.util.spec_from_file_location('snapshot_ipc', P)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)
FIELDS = ('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')

class Denied(RuntimeError):
    pass

def digest(obj):
    return hashlib.sha256(ipc.canonical(obj)).hexdigest()

def validate(monitors, target):
    if not isinstance(monitors, list) or len(monitors) not in (1,2):
        raise Denied('invalid topology')
    if target not in ('eDP-1','eDP-2'):
        raise Denied('invalid target')
    names = [m.get('name') for m in monitors if isinstance(m,dict)]
    if len(names) != len(monitors) or len(set(names)) != len(names) or target not in names or set(names) - {target,'HDMI-A-1'}:
        raise Denied('unexpected connectors')
    out=[]
    for m in monitors:
        if not all(k in m for k in FIELDS):
            raise Denied('missing snapshot field')
        item={k:m[k] for k in FIELDS}
        if any(type(item[k]) is not int or item[k] <= 0 for k in ('width','height')):
            raise Denied('invalid dimensions')
        if any(type(item[k]) is not int for k in ('x','y','transform')):
            raise Denied('invalid geometry')
        if type(item['scale']) not in (float,int) or not 0.5 <= item['scale'] <= 4:
            raise Denied('invalid scale')
        if type(item['refreshRate']) not in (float,int) or not 20 <= item['refreshRate'] <= 500:
            raise Denied('invalid refresh')
        if type(item['disabled']) is not bool or type(item['dpmsStatus']) is not bool:
            raise Denied('invalid power flags')
        if not isinstance(item['description'],str) or not item['description']:
            raise Denied('invalid description')
        out.append(item)
    out.sort(key=lambda x:x['name'])
    internal=next(m for m in out if m['name']==target)
    if (internal['width'],internal['height'],internal['x'],internal['y'],internal['scale'],internal['transform'],internal['disabled'],internal['dpmsStatus']) != (1920,1080,0,0,1,0,False,True):
        raise Denied('unsupported internal baseline')
    if not 59.95 <= internal['refreshRate'] <= 60.15:
        raise Denied('original mode not observed')
    return out

def identity(capture, target, topology):
    if not isinstance(capture,dict) or capture.get('decision') != 'DYNAMIC_TOPOLOGY_READONLY_READY':
        raise Denied('invalid capture')
    if capture.get('target') != target or capture.get('topology') != topology or capture.get('persistentSource') != 'config/monitors.lua':
        raise Denied('capture mismatch')
    if any(capture.get(k) is not False for k in ('captureAtomic','physicalExecutionEnabled','executionAuthorized')) or capture.get('commandsExecuted') != 0:
        raise Denied('capture claims authority')
    c=capture.get('compositor')
    if not isinstance(c,dict) or not isinstance(c.get('instance'),str) or not c['instance'] or type(c.get('pid')) is not int or type(c.get('starttime')) is not int:
        raise Denied('invalid compositor')
    for k in ('luaSha256','monitorDigest'):
        v=capture.get(k)
        if not isinstance(v,str) or len(v)!=64 or any(ch not in '0123456789abcdef' for ch in v):
            raise Denied('invalid digest')
    return {'instance':c['instance'],'pid':c['pid'],'starttime':c['starttime'],'lua_sha256':capture['luaSha256'],'capture_digest':digest(capture)}

def bind(root, monitors, capture):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
        if r.get('state') != 'PREPARED' or r.get('phase') != 'NONE' or r.get('generation') != 1:
            raise Denied('transaction not preparatory')
        if (p/'physical-snapshot.json').exists() or (p/'physical-snapshot.json').is_symlink():
            raise Denied('already bound')
        snapshot=validate(monitors,r.get('target'))
        topology='internal-only' if len(snapshot)==1 else 'internal-and-hdmi'
        if r.get('topology')!=topology:
            raise Denied('transaction topology mismatch')
        bound=identity(capture,r['target'],topology)
        side={'schema':1,'transaction_id':r['id'],'generation_at_binding':r['generation'],
              'boot_id':r['boot_id'],'target':r['target'],'topology':topology,
              'snapshot':snapshot,'snapshot_sha256':digest(snapshot),'identity':bound}
        ipc.write(p,'physical-snapshot.json',side)
        return {'decision':'PHYSICAL_SNAPSHOT_BOUND_READONLY','transactionId':r['id'],'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def inspect(root, monitors=None, capture=None):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json')
        s=ipc.read(p,'physical-snapshot.json')
        if (not isinstance(s,dict) or set(s)!={'schema','transaction_id','generation_at_binding','boot_id','target','topology','snapshot','snapshot_sha256','identity'} or s['schema']!=1 or
            s['transaction_id']!=r.get('id') or s['boot_id']!=r.get('boot_id') or s['target']!=r.get('target') or s['topology']!=r.get('topology') or
            type(r.get('generation')) is not int or r['generation']<s['generation_at_binding'] or
            digest(s['snapshot'])!=s['snapshot_sha256'] or validate(s['snapshot'],s['target'])!=s['snapshot']):
            raise Denied('binding inconsistent')
        if monitors is not None:
            if validate(monitors,s['target']) != s['snapshot']:
                raise Denied('observed snapshot drift')
        if capture is not None:
            if identity(capture,s['target'],s['topology']) != s['identity']:
                raise Denied('capture drift')
        return {'decision':'PHYSICAL_SNAPSHOT_CONSISTENT_READONLY','state':r['state'],'generation':r['generation'],'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['bind','inspect'])
    p.add_argument('root')
    p.add_argument('--monitors')
    p.add_argument('--capture')
    a=p.parse_args()
    try:
        monitors=json.loads(Path(a.monitors).read_text()) if a.monitors else None
        capture=json.loads(Path(a.capture).read_text()) if a.capture else None
        if a.action=='bind':
            if monitors is None or capture is None: raise Denied('missing evidence')
            result=bind(a.root,monitors,capture)
        else: result=inspect(a.root,monitors,capture)
        code=0
    except (OSError,RuntimeError,ValueError,TypeError,KeyError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
        code=2
    print(json.dumps(result,indent=2))
    return code
if __name__=='__main__': sys.exit(main())
