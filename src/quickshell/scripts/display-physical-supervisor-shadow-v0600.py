#!/usr/bin/env python3
"""Persistent READONLY supervisor shadow. Not a physical watchdog or authorization."""
import argparse, importlib.util, json, os, subprocess, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
P=HERE/'display-ipc-transaction-lab-v0600.py'
if P.is_symlink() or not P.is_file(): raise RuntimeError('unsafe IPC dependency')
spec=importlib.util.spec_from_file_location('shadow_ipc',P)
ipc=importlib.util.module_from_spec(spec);spec.loader.exec_module(ipc)
FIELDS=('name','description','width','height','refreshRate','x','y','scale','transform','disabled','dpmsStatus')
class Denied(RuntimeError): pass
def result(decision,**kw):
    return dict(version='0.6.0',mode='physical-supervisor-shadow-readonly',decision=decision,
                watchdogPhysicalArmed=False,physicalExecutionEnabled=False,executionAuthorized=False,
                commandsExecuted=0,rollbackPhysicalVerified=False,**kw)
def validate(root):
    with ipc.locked(root) as p:
        r=ipc.read(p,'transaction.json');s=ipc.read(p,'physical-snapshot.json')
        if r.get('execution_backend')!='physical' or r.get('state') not in ('ARMED','APPLIED','ROLLBACK_REQUIRED','UNCERTAIN','ROLLED_BACK'): raise Denied('backend or state')
        if r.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip(): raise Denied('boot drift')
        if s.get('transaction_id')!=r.get('id') or any(s.get(k)!=r.get(k) for k in ('boot_id','target','topology')): raise Denied('identity')
        saved=s.get('snapshot')
        if not isinstance(saved,list) or s.get('snapshot_sha256')!=__import__('hashlib').sha256(ipc.canonical(saved)).hexdigest(): raise Denied('digest')
        expected={r.get('target')} if r.get('topology')=='internal-only' else {r.get('target'),'HDMI-A-1'} if r.get('topology')=='internal-and-hdmi' else set()
        if r.get('target') not in ('eDP-1','eDP-2') or not expected or len(saved)!=len(expected) or any(not isinstance(m,dict) or any(k not in m for k in FIELDS) for m in saved) or {m['name'] for m in saved}!=expected: raise Denied('topology')
        identity=s.get('identity')
        if not isinstance(identity,dict) or not isinstance(identity.get('instance'),str) or not identity['instance']: raise Denied('instance')
        return r,s

def inspect(root,instances=None,monitors=None):
    r,s=validate(root)
    if instances is None or monitors is None: return result('SHADOW_TRANSACTION_VALID_READONLY',transactionId=r['id'],generation=r['generation'])
    if not isinstance(instances,list) or len(instances)!=1 or instances[0].get('instance')!=s['identity']['instance']: raise Denied('instance drift')
    if not isinstance(monitors,list) or len(monitors)!=len(s['snapshot']): raise Denied('monitor count')
    observed={m.get('name'):m for m in monitors if isinstance(m,dict)}
    original={m['name']:m for m in s['snapshot']}
    if len(observed)!=len(monitors) or set(observed)!=set(original): raise Denied('hotplug')
    for name,m in observed.items():
        old=original[name]
        if any(m.get(k)!=old[k] for k in FIELDS if k!='refreshRate'): raise Denied('geometry drift')
        hz=m.get('refreshRate')
        if type(hz) not in (int,float) or not __import__('math').isfinite(hz): raise Denied('refresh type')
        if name!=r['target'] and abs(hz-old['refreshRate'])>0.02: raise Denied('protected drift')
        if name==r['target'] and min(abs(hz-old['refreshRate']),abs(hz-144.06))>0.02: raise Denied('refresh drift')
    return result('SHADOW_LIVE_OBSERVATION_CONSISTENT',transactionId=r['id'],generation=r['generation'],target=r['target'])

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root');ap.add_argument('--live',action='store_true');a=ap.parse_args()
    try:
        if a.live:
            env=dict(os.environ);env.pop('WAYLAND_DISPLAY',None);env.pop('DISPLAY',None);env.pop('HYPRLAND_INSTANCE_SIGNATURE',None)
            env['XDG_RUNTIME_DIR']='/run/user/'+str(os.getuid())
            def query(args): return json.loads(subprocess.run(args,env=env,capture_output=True,text=True,check=True,timeout=8).stdout)
            instances=query(['hyprctl','instances','-j'])
            if not isinstance(instances,list) or len(instances)!=1: raise Denied('ambiguous instances')
            monitors=query(['hyprctl','-i',instances[0]['instance'],'monitors','-j'])
            response=inspect(a.root,instances,monitors)
        else: response=inspect(a.root)
        rc=0
    except (Denied,ipc.Denied,OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
        response=result('BLOCK_AND_INVESTIGATE',errorType=type(exc).__name__);rc=2
    print(json.dumps(response,indent=2));return rc
if __name__=='__main__':sys.exit(main())
