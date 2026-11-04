#!/usr/bin/env python3
"""Read-only command proposals for the topology capture. Never executes hyprctl."""
import argparse, hashlib, importlib.util, json, math
from pathlib import Path
HERE=Path(__file__).resolve().parent
class Denied(ValueError):pass

def plan(capture,monitors,lua_bytes):
    p=HERE/'display-dynamic-topology-v0600.py'
    if p.is_symlink() or not p.is_file():raise Denied('unsafe dependency')
    s=importlib.util.spec_from_file_location('topology_contract',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    if not isinstance(capture,dict) or capture.get('decision')!='DYNAMIC_TOPOLOGY_READONLY_READY':raise Denied('capture')
    if any(capture.get(k) is not False for k in ('captureAtomic','executionAuthorized','physicalExecutionEnabled')) or capture.get('commandsExecuted')!=0:raise Denied('authority')
    if not isinstance(lua_bytes,bytes) or hashlib.sha256(lua_bytes).hexdigest()!=capture.get('luaSha256'):raise Denied('Lua changed')
    if m.fingerprint(monitors)!=capture.get('monitorDigest'):raise Denied('topology changed')
    target=capture.get('target')
    if target not in ('eDP-1','eDP-2') or capture.get('alternate')!=('eDP-2' if target=='eDP-1' else 'eDP-1'):raise Denied('connector')
    if capture.get('protected')!='HDMI-A-1' or capture.get('persistentSource')!='config/monitors.lua':raise Denied('source')
    if capture.get('topology')!=('internal-only' if len(monitors)==1 else 'internal-and-hdmi'):raise Denied('topology label')
    internal=next(x for x in monitors if x['name']==target)
    if (internal.get('make')!='AU Optronics' or str(internal.get('model','')).strip()!='B156HAN15.H'
        or any(internal.get(k)!=v for k,v in {'width':1920,'height':1080,'scale':1,'transform':0,'disabled':False}.items())
        or type(internal.get('refreshRate')) not in (int,float) or not math.isfinite(internal['refreshRate']) or abs(internal['refreshRate']-60.061)>0.15
        or not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(internal.get('availableModes',[])))):raise Denied('internal mode')
    if len(monitors)==2:
        ext=next(x for x in monitors if x['name']=='HDMI-A-1')
        if ext.get('make')!='Dell Inc.' or str(ext.get('model','')).strip()!='Inspiron 3043' or ext.get('disabled') is not False:raise Denied('external identity')
    c=capture.get('compositor')
    if not isinstance(c,dict) or type(c.get('pid')) is not int or c['pid']<2 or type(c.get('starttime')) is not int or c['starttime']<1 or not isinstance(c.get('instance'),str) or not c['instance'] or '/' in c['instance'] or any(type(c.get(k)) is not int or c[k]<0 for k in ('socketDevice','socketInode')):raise Denied('compositor')
    x,y=internal.get('x'),internal.get('y')
    if type(x) is not int or type(y) is not int or abs(x)>32768 or abs(y)>32768:raise Denied('position')
    def cmd(hz):return ['hyprctl','-i',c['instance'],'keyword','monitor',f'{target},1920x1080@{hz},{x}x{y},1']
    return {'version':'0.6.0','decision':'DYNAMIC_TOPOLOGY_COMMANDS_READONLY','target':target,'topology':capture['topology'],
            'candidateArgs':cmd('144.06'),'rollbackArgs':cmd('60.06'),'protected':'HDMI-A-1',
            'requiresIndependentRollback':True,'requiresFreshCaptureBeforeExecution':True,
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--capture',required=True);p.add_argument('--monitors',required=True);p.add_argument('--lua-file',required=True);a=p.parse_args()
    try:r=plan(json.loads(Path(a.capture).read_text()),json.loads(Path(a.monitors).read_text()),Path(a.lua_file).read_bytes());rc=0
    except (OSError,ValueError,TypeError,KeyError) as e:r={'decision':'BLOCK_AND_INVESTIGATE','errorType':type(e).__name__,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0};rc=2
    print(json.dumps(r,indent=2));return rc
if __name__=='__main__':raise SystemExit(main())
