#!/usr/bin/env python3
"""Read-only physical executor admission gate. No monitor mutations."""
import argparse, hashlib, json, os, pathlib, subprocess, stat, sys

class Denied(Exception): pass

def safe_file(path):
    p=pathlib.Path(path)
    if p.is_symlink() or not p.is_file(): raise Denied('unsafe file')
    s=p.stat()
    if s.st_uid!=os.getuid() or not stat.S_ISREG(s.st_mode): raise Denied('unsafe owner/type')
    return p

def validate(snapshot, observed, config, config_hash):
    if hashlib.sha256(config).hexdigest()!=config_hash: raise Denied('config hash mismatch')
    if not isinstance(snapshot,list) or not isinstance(observed,list): raise Denied('invalid monitor arrays')
    a={m.get('name'):m for m in snapshot if isinstance(m,dict)}
    b={m.get('name'):m for m in observed if isinstance(m,dict)}
    if len(a)!=2 or len(b)!=2 or set(a)!=set(b) or set(a)!={'eDP-1','HDMI-A-1'}: raise Denied('topology mismatch')
    for name in a:
        old,new=a[name],b[name]
        for key in ('width','height','scale','transform','x','y','disabled'):
            if old.get(key)!=new.get(key): raise Denied(name+' '+key+' mismatch')
        if abs(float(old.get('refreshRate',-1))-float(new.get('refreshRate',-2)))>0.05: raise Denied(name+' refresh mismatch')
    edp=b['eDP-1']
    modes=edp.get('availableModes',[])
    if not isinstance(modes,list) or not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(modes)):
        raise Denied('target modes not advertised')
    if edp.get('width')!=1920 or edp.get('height')!=1080 or edp.get('disabled') is not False:
        raise Denied('unsafe internal monitor')
    return {'decision':'DRY_RUN_READY','monitor':'eDP-1','original':'1920x1080@60.06Hz','candidate':'1920x1080@144.06Hz','untouched':'HDMI-A-1','physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackVerified':False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--backup-dir',required=True)
    args=p.parse_args()
    try:
        root=pathlib.Path(args.backup_dir)
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid!=os.getuid(): raise Denied('unsafe backup root')
        snap=json.loads(safe_file(root/'monitors.json').read_text())
        conf=safe_file(root/'monitors.conf').read_bytes()
        lines=safe_file(root/'monitors.sha256').read_text().splitlines()
        if len(lines)!=1 or len(lines[0].split())!=2: raise Denied('invalid checksum manifest')
        digest=lines[0].split()[0]
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest): raise Denied('invalid checksum')
        current=safe_file(pathlib.Path.home()/'.config/hypr/monitors.conf').read_bytes()
        if current!=conf: raise Denied('active config differs from backup')
        result=subprocess.run(['hyprctl','monitors','-j'],capture_output=True,text=True,timeout=8,check=True)
        observed=json.loads(result.stdout)
        output=validate(snap,observed,conf,digest)
        print(json.dumps(output,indent=2)); return 0
    except (Denied,OSError,ValueError,TypeError,subprocess.SubprocessError) as exc:
        print(json.dumps({'decision':'BLOCK_AND_INVESTIGATE','error':str(exc),'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0})); return 2
if __name__=='__main__': sys.exit(main())
