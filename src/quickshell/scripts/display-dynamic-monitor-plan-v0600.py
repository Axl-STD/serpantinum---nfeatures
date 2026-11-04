#!/usr/bin/env python3
"""Read-only dynamic eDP planner. No physical execution or Lua evaluation."""
import argparse, hashlib, importlib.util, json, math, os, pathlib, subprocess, sys
HERE=pathlib.Path(__file__).resolve().parent
class Denied(RuntimeError): pass

def load(name, alias):
    p=HERE/name
    if p.is_symlink() or not p.is_file(): raise Denied('unsafe dependency')
    spec=importlib.util.spec_from_file_location(alias,p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def plan(snapshot, observed, lua_root, lua_backup, config_root, config_backup, uid=None):
    uid=os.getuid() if uid is None else uid
    identity=load('display-monitor-identity-lua-v0600.py','dynamic_identity')
    try: proof=identity.audit(lua_root,lua_backup,observed,uid)
    except identity.Denied as exc: raise Denied(str(exc)) from exc
    active=proof['activeInternalConnector']; external='HDMI-A-1'
    if not isinstance(snapshot,list) or not isinstance(observed,list): raise Denied('invalid snapshot')
    if len(snapshot)!=2 or len(observed)!=2: raise Denied('topology mismatch')
    def keyed(items):
        out={}
        for m in items:
            if not isinstance(m,dict) or not isinstance(m.get('name'),str) or m['name'] in out: raise Denied('duplicate or malformed output')
            out[m['name']]=m
        return out
    old=keyed(snapshot);new=keyed(observed)
    old_internal=[k for k in old if k in ('eDP-1','eDP-2')]
    if len(old_internal)!=1 or set(old)!={old_internal[0],external} or set(new)!={active,external}: raise Denied('unexpected output topology')
    if old_internal[0]!=active:
        # A prior snapshot from eDP-1 is NOT proof of eDP-2 equivalence.
        raise Denied('snapshot connector changed; recapture read-only baseline')
    for key in (active,external):
        for field in ('width','height','x','y','scale','transform','disabled'):
            if type(old[key].get(field)) is not type(new[key].get(field)) or old[key].get(field)!=new[key].get(field): raise Denied('topology drift')
        a,b=old[key].get('refreshRate'),new[key].get('refreshRate')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)) or abs(a-b)>0.05: raise Denied('refresh drift')
    edp=new[active]
    if edp.get('scale')!=1 or edp.get('transform')!=0 or edp.get('disabled') is not False: raise Denied('internal state invalid')
    if not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(edp.get('availableModes',[]))): raise Denied('modes unavailable')
    config_root=pathlib.Path(config_root);config_backup=pathlib.Path(config_backup)
    for p in (config_root,config_backup):
        if p.is_symlink() or not p.is_dir() or p.stat().st_uid!=uid: raise Denied('unsafe config directory')
    for name in ('monitors.conf','monitors.json','monitors.sha256'):
        p=config_backup/name
        if p.is_symlink() or not p.is_file() or p.stat().st_uid!=uid: raise Denied('unsafe baseline')
    raw=(config_backup/'monitors.conf').read_bytes()
    manifest=(config_backup/'monitors.sha256').read_text().splitlines()
    if len(manifest)!=1 or len(manifest[0].split())!=2 or manifest[0].split()[0]!=hashlib.sha256(raw).hexdigest() or manifest[0].split()[1] not in ('monitors.conf',str(config_backup/'monitors.conf')): raise Denied('baseline digest mismatch')
    conf=config_root/'monitors.conf'
    if conf.is_symlink() or not conf.is_file() or conf.read_bytes()!=raw: raise Denied('secondary config drift')
    if json.loads((config_backup/'monitors.json').read_text())!=snapshot: raise Denied('snapshot mismatch')
    return {'version':'0.6.0','decision':'DYNAMIC_READ_ONLY_PLAN_READY','target':active,'alternate':proof['alternateInternalConnector'],'protected':external,'persistentSource':proof['persistentSource'],'luaSha256':proof['luaSha256'],'original':'1920x1080@60.06Hz','candidate':'1920x1080@144.06Hz','configurationAdapter':'hyprland','supervisionBackend':'not-selected','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--hypr-dir',required=True);ap.add_argument('--lua-backup',required=True);ap.add_argument('--config-backup',required=True);args=ap.parse_args()
    try:
        backup=pathlib.Path(args.config_backup)
        snap=json.loads((backup/'monitors.json').read_text())
        observed=json.loads(subprocess.run(['hyprctl','monitors','-j'],capture_output=True,text=True,timeout=8,check=True).stdout)
        result=plan(snap,observed,args.hypr_dir,args.lua_backup,args.hypr_dir,backup);rc=0
    except (Denied,OSError,ValueError,TypeError,UnicodeError,subprocess.SubprocessError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'error':str(exc),'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0};rc=2
    print(json.dumps(result,indent=2));return rc
if __name__=='__main__':sys.exit(main())
