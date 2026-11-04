#!/usr/bin/env python3
"""Read-only live topology bridge for one internal or internal+HDMI outputs.

No hyprctl mutation, no persistent baseline writes, no execution authorization.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
class Denied(RuntimeError): pass

def load(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file(): raise Denied('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def fingerprint(monitors):
    if not isinstance(monitors, list) or not 1 <= len(monitors) <= 2 or not all(isinstance(x,dict) for x in monitors):
        raise Denied('unsupported monitor count')
    names=[m.get('name') for m in monitors]
    if len(set(names)) != len(names) or len(set(names)&{'eDP-1','eDP-2'}) != 1 or set(names)-{'eDP-1','eDP-2','HDMI-A-1'}:
        raise Denied('ambiguous topology')
    return hashlib.sha256(json.dumps(sorted(monitors,key=lambda m:m['name']),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def validate(monitors, hypr_dir, lua_backup, config_backup):
    identity = load('display-monitor-identity-lua-v0600.py','topology_identity')
    uid=os.getuid()
    root=Path(hypr_dir); backup=Path(lua_backup)
    if root.is_symlink() or backup.is_symlink() or not root.is_dir() or not backup.is_dir():raise Denied('unsafe Lua directory')
    main=identity.regular(root/'hyprland.lua',uid)
    lua=identity.regular(root/'config/monitors.lua',uid)
    if main!=identity.regular(backup/'hyprland.lua',uid) or lua!=identity.regular(backup/'config/monitors.lua',uid):raise Denied('Lua drift')
    import re
    if not re.search(rb'^\s*require\s*\(\s*["\']config/monitors["\']\s*\)',main,re.M):raise Denied('missing Lua import')
    rules=identity.parse_rules(lua.decode('utf-8'))
    if not {'eDP-1','eDP-2','HDMI-A-1'}.issubset(rules):raise Denied('missing Lua fallback')
    fingerprint(monitors)
    edp=next(m for m in monitors if m['name'] in ('eDP-1','eDP-2'))
    if (edp.get('make')!='AU Optronics' or str(edp.get('model','')).strip()!='B156HAN15.H'
        or any(edp.get(k)!=v for k,v in {'width':1920,'height':1080,'scale':1,'transform':0,'disabled':False}.items())
        or type(edp.get('refreshRate')) not in (int,float) or not math.isfinite(edp['refreshRate'])
        or abs(edp['refreshRate']-60.061)>0.15
        or not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(edp.get('availableModes',[])))):
        raise Denied('internal identity or mode')
    if len(monitors)==2:
        ext=next(m for m in monitors if m['name']=='HDMI-A-1')
        if ext.get('make')!='Dell Inc.' or str(ext.get('model','')).strip()!='Inspiron 3043' or ext.get('disabled') is not False:
            raise Denied('external identity')
    # Historical two-output backup is verified but NEVER treated as a current mono snapshot.
    cb=Path(config_backup)
    if cb.is_symlink() or not cb.is_dir():raise Denied('unsafe baseline')
    raw=identity.regular(cb/'monitors.conf',uid)
    manifest=identity.regular(cb/'monitors.sha256',uid).decode().splitlines()
    if len(manifest)!=1 or len(manifest[0].split())!=2 or manifest[0].split()[0]!=hashlib.sha256(raw).hexdigest():raise Denied('baseline hash')
    if manifest[0].split()[1] not in ('monitors.conf',str(cb/'monitors.conf')):raise Denied('baseline manifest')
    if identity.regular(root/'monitors.conf',uid)!=raw:raise Denied('secondary config drift')
    historical=json.loads(identity.regular(cb/'monitors.json',uid))
    if not isinstance(historical,list) or len(historical)!=2:raise Denied('historical baseline')
    return {'target':edp['name'],'alternate':'eDP-2' if edp['name']=='eDP-1' else 'eDP-1',
            'luaSha256':hashlib.sha256(lua).hexdigest(),'monitorDigest':fingerprint(monitors),
            'topology':'internal-only' if len(monitors)==1 else 'internal-and-hdmi',
            'historicalBaselineIsCurrent':historical==monitors}

def capture(hypr_dir,lua_backup,config_backup,runtime=None,uid=None,runner=subprocess.run):
    legacy=load('display-live-authority-capture-v0600.py','topology_capture')
    uid=os.getuid() if uid is None else uid
    runtime=Path(runtime or os.environ.get('XDG_RUNTIME_DIR','/run/user/'+str(uid)))
    st=runtime.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=uid:raise Denied('unsafe runtime')
    env=dict(os.environ);env['XDG_RUNTIME_DIR']=str(runtime)
    env.pop('HYPRLAND_INSTANCE_SIGNATURE',None);env.pop('WAYLAND_DISPLAY',None)
    def query(args):
        result=runner(args,env=env,capture_output=True,text=True,timeout=10,check=True)
        return json.loads(result.stdout)
    before=legacy.identity(query(['hyprctl','instances','-j']),runtime,uid)
    env['HYPRLAND_INSTANCE_SIGNATURE']=before['instance']
    first=query(['hyprctl','monitors','-j'])
    proof=validate(first,hypr_dir,lua_backup,config_backup)
    after=legacy.identity(query(['hyprctl','instances','-j']),runtime,uid)
    second=query(['hyprctl','monitors','-j'])
    proof2=validate(second,hypr_dir,lua_backup,config_backup)
    if before!=after or proof!=proof2:raise Denied('topology or compositor changed during capture')
    return {'version':'0.6.0','decision':'DYNAMIC_TOPOLOGY_READONLY_READY','compositor':before,
            'target':proof['target'],'alternate':proof['alternate'],'protected':'HDMI-A-1',
            'topology':proof['topology'],'historicalBaselineIsCurrent':proof['historicalBaselineIsCurrent'],
            'persistentSource':'config/monitors.lua','luaSha256':proof['luaSha256'],
            'monitorDigest':proof['monitorDigest'],'captureAtomic':False,
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--hypr-dir',required=True);p.add_argument('--lua-backup',required=True);p.add_argument('--config-backup',required=True)
    a=p.parse_args()
    try: result=capture(a.hypr_dir,a.lua_backup,a.config_backup);code=0
    except (Denied,OSError,ValueError,TypeError,UnicodeError,subprocess.SubprocessError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'reason':str(exc),'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0};code=2
    print(json.dumps(result,indent=2));return code
if __name__=='__main__':sys.exit(main())
