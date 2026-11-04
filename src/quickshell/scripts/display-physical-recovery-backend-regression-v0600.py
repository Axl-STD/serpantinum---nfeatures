#!/usr/bin/env python3
"""Offline regression of physical backend classification, no display mutations."""
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile

HERE=Path(__file__).resolve().parent

def load(name,alias):
    p=HERE/name
    spec=importlib.util.spec_from_file_location(alias,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

ipc=load('display-ipc-transaction-lab-v0600.py','phys_test_ipc')
backend=load('display-physical-recovery-backend-v0600.py','phys_test_backend')
BASE={'name':'eDP-1','description':'Panel Internal','width':1920,'height':1080,'refreshRate':60.061,'x':0,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True}
HDMI={'name':'HDMI-A-1','description':'External Monitor','width':1920,'height':1080,'refreshRate':60.,'x':1920,'y':0,'scale':1,'transform':0,'disabled':False,'dpmsStatus':True}
results=[]

def case(name,modify=None,decision=None,denied=False,dual=False,target='eDP-1'):
    import copy
    with tempfile.TemporaryDirectory(prefix='serpantinum-physical-backend-') as d:
        root=Path(d)
        root.chmod(0o700)
        original=copy.deepcopy(BASE)
        original['name']=target
        snap=[original]+([copy.deepcopy(HDMI)] if dual else [])
        r={'schema':1,'id':secrets.token_hex(32),'generation':4,'state':'APPLIED','phase':'APPLY_VERIFIED','execution_backend':'physical','target':target,'topology':'internal-and-hdmi' if dual else 'internal-only','boot_id':'test-boot'}
        s={'schema':1,'transaction_id':r['id'],'generation_at_binding':1,'boot_id':'test-boot','target':target,'topology':r['topology'],'snapshot':snap,'snapshot_sha256':backend.digest(snap),'identity':{'instance':'test-instance','lua_sha256':'a'*64}}
        observed=copy.deepcopy(snap)
        if modify:
            modify(r,s,observed)
        with ipc.locked(root) as p:
            ipc.write(p,'transaction.json',r)
            ipc.write(p,'physical-snapshot.json',s)
        try:
            out=backend.evaluate(root,observed,current_instance='test-instance',current_lua_sha256='a'*64)
            ok=(not denied and out['decision']==decision and out['commandsExecuted']==0 and out['rollbackPhysicalVerified'] is False)
        except (backend.Denied,ipc.Denied,KeyError,ValueError,TypeError):
            ok=denied
        results.append({'scenario':name,'passed':bool(ok)})

def change(field,value):
    return lambda r,s,o:o[0].__setitem__(field,value)

def copy_monitor():
    return dict(HDMI)

for dual in (False,True):
    for target in ('eDP-1','eDP-2'):
        tag=target+('-dual' if dual else '-single')
        case(tag+'-original',decision='ORIGINAL_OBSERVED_PHYSICAL_UNVERIFIED',dual=dual,target=target)
        case(tag+'-candidate',change('refreshRate',144.06),'CANDIDATE_OBSERVED_RECOVERY_REQUIRED',dual=dual,target=target)
        case(tag+'-unknown',change('refreshRate',120.0),'BLOCK_UNEXPECTED_MODE',dual=dual,target=target)
        case(tag+'-position',change('x',100),'BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',dual=dual,target=target)
        case(tag+'-scale',change('scale',1.25),'BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',dual=dual,target=target)
        case(tag+'-resolution',change('width',1280),'BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',dual=dual,target=target)
        case(tag+'-identity',change('description','Other'),'BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',dual=dual,target=target)
        case(tag+'-backend',lambda r,s,o:r.__setitem__('execution_backend','simulated'),denied=True,dual=dual,target=target)
        case(tag+'-snapshot-hash',lambda r,s,o:s.__setitem__('snapshot_sha256','0'*64),denied=True,dual=dual,target=target)
        case(tag+'-txid',lambda r,s,o:s.__setitem__('transaction_id','wrong'),denied=True,dual=dual,target=target)
        case(tag+'-boot',lambda r,s,o:s.__setitem__('boot_id','wrong'),denied=True,dual=dual,target=target)
        case(tag+'-generation',lambda r,s,o:s.__setitem__('generation_at_binding',9),denied=True,dual=dual,target=target)
        case(tag+'-hotplug',lambda r,s,o:o.append(copy_monitor()),'BLOCK_TOPOLOGY_CHANGED',dual=dual,target=target)
        case(tag+'-disabled',change('disabled',True),'BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',dual=dual,target=target)
        case(tag+'-transform',change('transform',1),'BLOCK_GEOMETRY_OR_IDENTITY_CHANGED',dual=dual,target=target)

def copy_monitor():
    return dict(HDMI)

# Report exactly what ran, never hardcode a pass.
passed=all(x['passed'] for x in results)
print(json.dumps({'selftest':'passed' if passed else 'failed','totalScenarios':len(results),'results':results,'physicalExecutionEnabled':False,'commandsExecuted':0},indent=2))
sys.exit(0 if passed else 1)
