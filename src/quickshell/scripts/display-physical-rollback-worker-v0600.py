#!/usr/bin/env python3
"""Physical rollback boundary, strictly non-mutating in v0.6.0.

Live mode validates compositor and monitor readback. Offline mode checks a
fixture. Neither mode issues Hyprland mutations or claims physical recovery.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MODES = {'1920x1080@60.06', '1920x1080@144.06'}

class Denied(RuntimeError):
    pass

def result(decision, detail=None, **fields):
    return dict(version='0.6.0', mode='physical-rollback-readonly', decision=decision,
                detail=detail, physicalExecutionEnabled=False,
                executionAuthorized=False, commandsExecuted=0,
                rollbackPhysicalVerified=False, **fields)

def validate(monitors, target, expected_mode, instance):
    if target not in ('eDP-1','eDP-2') or expected_mode not in MODES:
        raise Denied('invalid target or mode')
    if not isinstance(instance,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,160}',instance):
        raise Denied('invalid compositor identity')
    if not isinstance(monitors,list) or not 1 <= len(monitors) <= 2:
        raise Denied('unexpected topology')
    if not all(isinstance(m,dict) and isinstance(m.get('name'),str) for m in monitors):
        raise Denied('malformed monitor')
    by_name = {m['name']:m for m in monitors}
    if len(by_name)!=len(monitors) or set(by_name) not in ({target}, {target,'HDMI-A-1'}):
        raise Denied('topology changed')
    internal=by_name[target]
    if internal.get('disabled') is not False or internal.get('width')!=1920 or internal.get('height')!=1080:
        raise Denied('internal display changed')
    if not isinstance(internal.get('availableModes'),list) or not {'1920x1080@60.06Hz','1920x1080@144.06Hz'}.issubset(set(internal['availableModes'])):
        raise Denied('modes changed')
    hz=internal.get('refreshRate')
    if type(hz) not in (int,float) or not (59.95 <= hz <= 60.15 or 143.95 <= hz <= 144.15):
        raise Denied('unexpected refresh rate')
    observed='1920x1080@60.06' if hz < 100 else '1920x1080@144.06'
    return result('ORIGINAL_MODE_OBSERVED_READONLY' if observed == '1920x1080@60.06' else 'CANDIDATE_MODE_OBSERVED_READONLY',
                  observedMode=observed, target=target, topology='internal-only' if len(monitors)==1 else 'internal-and-hdmi')

def query(instance, runner=subprocess.run):
    env={k:v for k,v in os.environ.items() if k not in ('WAYLAND_DISPLAY','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE','PYTHONPATH')}
    env['HYPRLAND_INSTANCE_SIGNATURE']=instance
    env['XDG_RUNTIME_DIR']=f'/run/user/{os.getuid()}'
    command=['hyprctl','-i',instance,'monitors','-j']
    p=runner(command, env=env, capture_output=True, text=True, timeout=8, check=True)
    return json.loads(p.stdout)

def execute(target, instance, fixture=None, runner=subprocess.run):
    if fixture is None:
        # Read-only live path; never calls keyword/dispatch.
        monitors=query(instance,runner)
    else:
        p=Path(fixture)
        if p.is_symlink() or not p.is_file() or p.stat().st_uid!=os.getuid() or p.stat().st_size>65536:
            raise Denied('unsafe fixture')
        monitors=json.loads(p.read_text())
    return validate(monitors,target,'1920x1080@60.06',instance)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target',required=True)
    ap.add_argument('--instance',required=True)
    ap.add_argument('--fixture')
    args=ap.parse_args()
    try:
        out=execute(args.target,args.instance,args.fixture)
        code=0
    except (Denied,OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
        out=result('BLOCK_AND_INVESTIGATE',type(exc).__name__)
        code=2
    print(json.dumps(out,indent=2))
    return code
if __name__=='__main__':
    sys.exit(main())
