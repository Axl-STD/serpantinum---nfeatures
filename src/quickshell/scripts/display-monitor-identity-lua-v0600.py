#!/usr/bin/env python3
"""Read-only Hyprland monitor identity and Lua source audit; never executes changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

class Denied(RuntimeError):
    pass

def regular(path, uid):
    if path.is_symlink() or not path.is_file():
        raise Denied('missing or linked file: '+str(path))
    s=path.stat()
    if s.st_uid!=uid or not stat.S_ISREG(s.st_mode):
        raise Denied('unsafe file: '+str(path))
    return path.read_bytes()

def parse_rules(text):
    # Deliberately strict: audit only literal hl.monitor tables, never evaluate Lua.
    clean=re.sub(r'--[^\n]*', '', text)
    blocks=re.findall(r'hl\.monitor\s*\(\s*\{(.*?)\}\s*\)',clean,re.S)
    if not blocks: raise Denied('no literal monitor rules')
    out={}
    for block in blocks:
        fields={}
        for k,v in re.findall(r'\b(output|mode|position|scale)\s*=\s*("[^"]*"|\d+(?:\.\d+)?)',block):
            if k in fields: raise Denied('duplicate rule field')
            fields[k]=v.strip('"')
        if set(fields)!={'output','mode','position','scale'}: raise Denied('unsupported rule shape')
        key=fields['output']
        if key in out: raise Denied('duplicate output')
        out[key]=fields
    return out

def audit(root, backup, observed, uid=None):
    uid=os.getuid() if uid is None else uid
    root=Path(root); backup=Path(backup)
    if root.is_symlink() or backup.is_symlink(): raise Denied('linked directory')
    primary=regular(root/'hyprland.lua',uid)
    config=regular(root/'config/monitors.lua',uid)
    if primary!=regular(backup/'hyprland.lua',uid) or config!=regular(backup/'config/monitors.lua',uid):
        raise Denied('Lua backup differs')
    if not re.search(r'^\s*require\s*\(\s*["\']config/monitors["\']\s*\)',primary.decode(),re.M):
        raise Denied('monitor source not imported')
    rules=parse_rules(config.decode())
    if not {'eDP-1','eDP-2','HDMI-A-1'}.issubset(rules): raise Denied('missing fallback rules')
    if not isinstance(observed,list): raise Denied('invalid observed monitors')
    if len(observed)!=2 or not all(isinstance(m,dict) for m in observed): raise Denied('unexpected topology')
    names=[m.get('name') for m in observed]
    if len(set(names))!=2 or 'HDMI-A-1' not in names: raise Denied('external monitor absent')
    internals=[m for m in observed if m.get('name') in ('eDP-1','eDP-2')]
    if len(internals)!=1: raise Denied('ambiguous internal monitor')
    internal=internals[0]
    if internal.get('make')!='AU Optronics' or str(internal.get('model','')).strip()!='B156HAN15.H':
        raise Denied('internal identity mismatch')
    if internal.get('width')!=1920 or internal.get('height')!=1080 or internal.get('disabled') is not False:
        raise Denied('unexpected internal state')
    external=next(m for m in observed if m.get('name')=='HDMI-A-1')
    if external.get('make')!='Dell Inc.' or str(external.get('model','')).strip()!='Inspiron 3043' or external.get('disabled') is not False:
        raise Denied('external identity mismatch')
    if not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(internal.get('availableModes',[]))):
        raise Denied('internal modes missing')
    return {'version':'0.6.0','decision':'LUA_IDENTITY_READONLY_READY','activeInternalConnector':internal['name'],
            'alternateInternalConnector':({'eDP-1':'eDP-2','eDP-2':'eDP-1'}[internal['name']]),
            'persistentSource':'config/monitors.lua','sourceVerifiedByImport':True,
            'luaSha256':hashlib.sha256(config).hexdigest(), 'protected':'HDMI-A-1',
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,
            'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--hypr-dir',required=True)
    p.add_argument('--backup-dir',required=True)
    args=p.parse_args()
    try:
        cmd=subprocess.run(['hyprctl','monitors','-j'],capture_output=True,text=True,timeout=8,check=True)
        result=audit(args.hypr_dir,args.backup_dir,json.loads(cmd.stdout))
        rc=0
    except (Denied,OSError,ValueError,UnicodeError,subprocess.SubprocessError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'error':str(exc),
                'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
        rc=2
    print(json.dumps(result,indent=2));return rc

if __name__=='__main__':sys.exit(main())
