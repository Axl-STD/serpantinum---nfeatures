#!/usr/bin/env python3
"""Pure offline Hyprland command contract. Never executes commands."""
import argparse
import hashlib
import json
import re
import sys

class Denied(ValueError):
    pass


def plan(capture, monitors, lua_bytes, expected_lua_hash, original_connector=None):
    if not isinstance(capture, dict) or capture.get('decision') != 'LIVE_AUTHORITY_READONLY_READY':
        raise Denied('capture rejected')
    if any(capture.get(k) is not False for k in ('captureAtomic', 'executionAuthorized', 'physicalExecutionEnabled')):
        raise Denied('capture claims authority')
    if type(capture.get('commandsExecuted')) is not int or capture['commandsExecuted'] != 0:
        raise Denied('invalid command count')
    target = capture.get('target')
    if target not in ('eDP-1', 'eDP-2') or capture.get('alternate') != ({'eDP-1','eDP-2'} - {target}).pop():
        raise Denied('connector mismatch')
    if original_connector is not None and original_connector != target:
        raise Denied('connector changed since snapshot')
    if capture.get('protected') != 'HDMI-A-1' or capture.get('persistentSource') != 'config/monitors.lua':
        raise Denied('protected/source mismatch')
    if not isinstance(lua_bytes, bytes) or not re.fullmatch(r'[a-f0-9]{64}', str(expected_lua_hash)):
        raise Denied('invalid lua proof')
    digest = hashlib.sha256(lua_bytes).hexdigest()
    if digest != expected_lua_hash or capture.get('luaSha256') != digest:
        raise Denied('lua changed')
    if not isinstance(monitors, list) or len(monitors) != 2 or not all(isinstance(m, dict) for m in monitors):
        raise Denied('topology')
    by_name = {m.get('name'): m for m in monitors}
    if len(by_name) != 2 or set(by_name) != {target, 'HDMI-A-1'}:
        raise Denied('topology changed')
    observed_digest = hashlib.sha256(json.dumps(sorted(monitors,key=lambda m:m['name']),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if capture.get('monitorDigest') != observed_digest:
        raise Denied('monitor observation changed')
    internal = by_name[target]
    external = by_name['HDMI-A-1']
    if internal.get('make') != 'AU Optronics' or internal.get('model','').strip() != 'B156HAN15.H':
        raise Denied('internal identity mismatch')
    if external.get('make') != 'Dell Inc.' or external.get('model') != 'Inspiron 3043':
        raise Denied('external identity mismatch')
    if any(internal.get(k) != v for k,v in {'width':1920,'height':1080,'scale':1,'transform':0,'disabled':False}.items()):
        raise Denied('internal geometry')
    if abs(internal.get('refreshRate',-100) - 60.061) > 0.15:
        raise Denied('baseline refresh mismatch')
    modes = internal.get('availableModes')
    if not isinstance(modes, list) or not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(modes)):
        raise Denied('modes unavailable')
    c = capture.get('compositor')
    if not isinstance(c,dict) or type(c.get('pid')) is not int or c['pid'] < 2 or type(c.get('starttime')) is not int or c['starttime'] < 1 or not isinstance(c.get('instance'),str) or not c['instance']:
        raise Denied('compositor identity')
    for k in ('socketDevice','socketInode'):
        if type(c.get(k)) is not int or c[k] < 0:
            raise Denied('socket identity')
    # The exact IPC commands are proposals only; not executable authorization.
    return {'version':'0.6.0','decision':'PHYSICAL_COMMAND_CONTRACT_READONLY',
            'target':target,'protected':'HDMI-A-1','luaSha256':digest,
            'candidateArgs':['hyprctl','-i',c['instance'],'keyword','monitor',f'{target},1920x1080@144.06,{internal.get("x",0)}x{internal.get("y",0)},1'],
            'rollbackArgs':['hyprctl','-i',c['instance'],'keyword','monitor',f'{target},1920x1080@60.06,{internal.get("x",0)}x{internal.get("y",0)},1'],
            'requiresIndependentRollback':True,'requiresFreshCaptureBeforeExecution':True,
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',required=True)
    p.add_argument('--monitors',required=True)
    p.add_argument('--lua-file',required=True)
    p.add_argument('--lua-sha256',required=True)
    args=p.parse_args()
    try:
        from pathlib import Path
        result=plan(json.loads(Path(args.capture).read_text()),json.loads(Path(args.monitors).read_text()),Path(args.lua_file).read_bytes(),args.lua_sha256)
        code=0
    except (OSError,ValueError,TypeError,KeyError) as exc:
        result={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
        code=2
    print(json.dumps(result,indent=2))
    return code

if __name__=='__main__':sys.exit(main())
