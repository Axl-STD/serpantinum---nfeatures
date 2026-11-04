#!/usr/bin/env python3
"""Fail-closed Hyprland transport contract. Real operations are READ ONLY.

Simulation accepts an injected transport for command/observation rehearsal.
No CLI, flag, or environment variable can enable physical writes.
"""
import argparse
import json
import math
import re
import subprocess
import sys

class Denied(RuntimeError):
    pass

FIELDS = ('name','width','height','x','y','scale','transform','disabled','dpmsStatus')
INSTANCE = re.compile(r'[A-Za-z0-9_-]{8,160}\Z')


def snapshot(monitors, target):
    if target not in ('eDP-1','eDP-2') or not isinstance(monitors,list) or len(monitors) not in (1,2):
        raise Denied('topology')
    if not all(isinstance(m,dict) and isinstance(m.get('name'),str) for m in monitors):
        raise Denied('monitor format')
    by = {m['name']:m for m in monitors}
    if len(by)!=len(monitors) or set(by) not in ({target},{target,'HDMI-A-1'}):
        raise Denied('topology drift')
    for m in monitors:
        for key in FIELDS:
            if key not in m:
                raise Denied('incomplete snapshot')
        if type(m['width']) is not int or type(m['height']) is not int or m['width']<=0 or m['height']<=0:
            raise Denied('dimensions')
        if type(m['x']) is not int or type(m['y']) is not int or type(m['transform']) is not int:
            raise Denied('geometry')
        if type(m['scale']) not in (int,float) or not math.isfinite(m['scale']) or m['scale']<=0:
            raise Denied('scale')
        if type(m['disabled']) is not bool or type(m['dpmsStatus']) is not bool:
            raise Denied('flags')
    m=by[target]
    if m['disabled'] or not m['dpmsStatus'] or (m['width'],m['height'])!=(1920,1080):
        raise Denied('internal state')
    hz=m.get('refreshRate')
    if type(hz) not in (int,float) or not math.isfinite(hz):
        raise Denied('frequency')
    if 59.95<=hz<=60.15: mode='original'
    elif 143.95<=hz<=144.15: mode='candidate'
    else: raise Denied('frequency drift')
    return {'target':target,'topology':sorted(by),'mode':mode,
            'geometry':{k:m[k] for k in FIELDS},
            'protected':[{k:by['HDMI-A-1'][k] for k in FIELDS}] if 'HDMI-A-1' in by else []}


def compare(before, after):
    if before['target']!=after['target'] or before['topology']!=after['topology'] or before['geometry']!=after['geometry'] or before['protected']!=after['protected']:
        raise Denied('snapshot changed')
    return after['mode']


def command(instance,target,mode,geometry):
    if not isinstance(instance,str) or not INSTANCE.fullmatch(instance): raise Denied('instance')
    if target not in ('eDP-1','eDP-2') or mode not in ('original','candidate'): raise Denied('mode')
    if geometry['name']!=target: raise Denied('connector')
    hz='60.06' if mode=='original' else '144.06'
    return ['hyprctl','-i',instance,'keyword','monitor',
            f'{target},1920x1080@{hz},{geometry["x"]}x{geometry["y"]},{geometry["scale"]}']


def rehearse(before, target, instance, observe, mutate):
    """Injected simulation only. Never call with real Hyprland mutator."""
    baseline=snapshot(before,target)
    if baseline['mode']!='candidate': raise Denied('not candidate')
    args=command(instance,target,'original',baseline['geometry'])
    observed=snapshot(observe(),target)
    if compare(baseline,observed)!='candidate': raise Denied('pre-command drift')
    mutate(args)
    result=snapshot(observe(),target)
    if compare(baseline,result)!='original': raise Denied('rollback not observed')
    return {'decision':'SIMULATED_ROLLBACK_OBSERVED','physicalExecutionEnabled':False,
            'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}


def live(instance,target,runner=subprocess.run):
    if not isinstance(instance,str) or not INSTANCE.fullmatch(instance): raise Denied('instance')
    p=runner(['hyprctl','-i',instance,'monitors','-j'],capture_output=True,text=True,timeout=8,check=True)
    state=snapshot(json.loads(p.stdout),target)
    return {'decision':'LIVE_TRANSPORT_READONLY','snapshot':state,'physicalExecutionEnabled':False,
            'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--instance',required=True)
    ap.add_argument('--target',required=True)
    a=ap.parse_args()
    try: result=live(a.instance,a.target);rc=0
    except (Denied,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        result={'decision':'BLOCK_AND_INVESTIGATE','physicalExecutionEnabled':False,
                'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False};rc=2
    print(json.dumps(result,indent=2));return rc

if __name__=='__main__':sys.exit(main())
