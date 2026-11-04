#!/usr/bin/env python3
"""Read-only Hyprland operation planner. Never invokes a monitor-changing command."""
import argparse, hashlib, json, math, os, pathlib, subprocess, sys

class Denied(RuntimeError): pass

def by_name(items):
    if not isinstance(items,list) or len(items)!=2 or any(not isinstance(x,dict) or not isinstance(x.get('name'),str) for x in items): raise Denied('invalid monitors')
    found={x['name']:x for x in items}
    if set(found)!={'eDP-1','HDMI-A-1'}:raise Denied('topology mismatch')
    return found

def plan(snapshot,observed,config,expected_hash):
    if hashlib.sha256(config).hexdigest()!=expected_hash:raise Denied('config digest mismatch')
    old,new=by_name(snapshot),by_name(observed)
    for name in old:
        for key in ('width','height','x','y','scale','transform','disabled'):
            if old[name].get(key)!=new[name].get(key):raise Denied('topology drift: '+name+' '+key)
        for value in (old[name].get('refreshRate'),new[name].get('refreshRate')):
            if type(value) not in (int,float) or not math.isfinite(value):raise Denied('invalid refresh')
        if abs(old[name]['refreshRate']-new[name]['refreshRate'])>0.05:raise Denied('refresh drift')
    edp=new['eDP-1']
    if edp['disabled'] is not False or edp['width']!=1920 or edp['height']!=1080 or edp['scale']!=1 or edp['transform']!=0:raise Denied('invalid internal display')
    if not isinstance(edp.get('availableModes'),list) or not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(edp['availableModes'])):raise Denied('modes unavailable')
    return {'version':'0.6.0','decision':'READ_ONLY_PLAN_READY','target':'eDP-1','protected':'HDMI-A-1','original':'1920x1080@60.06Hz','candidate':'1920x1080@144.06Hz','configurationAdapter':'hyprland','supervisionBackend':'not-selected','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--backup-dir',required=True);args=p.parse_args()
    try:
        root=pathlib.Path(args.backup_dir)
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid!=os.getuid():raise Denied('unsafe backup directory')
        def read(name):
            f=root/name
            if f.is_symlink() or not f.is_file() or f.stat().st_uid!=os.getuid():raise Denied('unsafe backup file')
            return f.read_bytes()
        snapshot=json.loads(read('monitors.json'))
        config=read('monitors.conf')
        manifest=read('monitors.sha256').decode().splitlines()
        if len(manifest)!=1 or len(manifest[0].split())!=2 or manifest[0].split()[1] not in ('monitors.conf',str(root/'monitors.conf')):raise Denied('invalid manifest')
        active=pathlib.Path.home()/'.config/hypr/monitors.conf'
        if active.is_symlink() or active.read_bytes()!=config:raise Denied('active config mismatch')
        result=subprocess.run(['hyprctl','monitors','-j'],capture_output=True,text=True,timeout=8,check=True)
        output=plan(snapshot,json.loads(result.stdout),config,manifest[0].split()[0])
        code=0
    except (OSError,ValueError,TypeError,Denied,subprocess.SubprocessError) as exc:
        output={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0};code=2
    print(json.dumps(output,indent=2));return code
if __name__=='__main__':sys.exit(main())
