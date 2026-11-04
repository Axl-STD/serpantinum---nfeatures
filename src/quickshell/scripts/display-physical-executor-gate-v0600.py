#!/usr/bin/env python3
"""Fail-closed physical execution preflight. READ ONLY. Never executes monitor changes.

Physical execution is intentionally not exposed: the existing IPC engine is a
simulator and its files are not evidence of actual Hyprland restoration.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent

class Denied(RuntimeError):
    pass

def load(filename, alias):
    path = HERE / filename
    if path.is_symlink() or not path.is_file():
        raise Denied('missing or unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def check(plan, capture, monitors, lua_bytes, instances, fresh_monitors, *, watchdog=None):
    """Pure preflight, with injected read-only observations for regression testing."""
    if not isinstance(plan, dict) or plan.get('decision') != 'DYNAMIC_TOPOLOGY_COMMANDS_READONLY':
        raise Denied('plan')
    if not isinstance(capture, dict) or capture.get('decision') != 'DYNAMIC_TOPOLOGY_READONLY_READY':
        raise Denied('capture')
    if any(plan.get(k) is not False for k in ('physicalExecutionEnabled', 'executionAuthorized')):
        raise Denied('plan authority')
    if any(capture.get(k) is not False for k in ('physicalExecutionEnabled', 'executionAuthorized', 'captureAtomic')):
        raise Denied('capture authority')
    if type(plan.get('commandsExecuted')) is not int or plan['commandsExecuted'] != 0:
        raise Denied('plan commands')
    if type(capture.get('commandsExecuted')) is not int or capture['commandsExecuted'] != 0:
        raise Denied('capture commands')
    if not isinstance(lua_bytes, bytes) or hashlib.sha256(lua_bytes).hexdigest() != capture.get('luaSha256'):
        raise Denied('lua drift')
    if not isinstance(monitors, list) or not isinstance(fresh_monitors, list) or monitors != fresh_monitors:
        raise Denied('monitor drift')
    if not isinstance(instances, list) or len(instances) != 1 or not isinstance(instances[0], dict):
        raise Denied('ambiguous compositor')
    compositor = capture.get('compositor')
    if not isinstance(compositor, dict) or instances[0].get('instance') != compositor.get('instance') or instances[0].get('pid') != compositor.get('pid'):
        raise Denied('compositor drift')
    if not isinstance(compositor.get('instance'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{8,160}', compositor['instance']):
        raise Denied('invalid instance')
    if plan.get('target') != capture.get('target') or plan.get('topology') != capture.get('topology') or plan.get('protected') != 'HDMI-A-1':
        raise Denied('target drift')
    target = plan['target']
    if target not in ('eDP-1', 'eDP-2'):
        raise Denied('target')
    if capture.get('topology') not in ('internal-only', 'internal-and-hdmi'):
        raise Denied('topology')
    names = [m.get('name') for m in monitors if isinstance(m, dict)]
    if len(names) != len(monitors) or len(set(names)) != len(names) or set(names) != ({target} if capture['topology'] == 'internal-only' else {target, 'HDMI-A-1'}):
        raise Denied('topology drift')
    internal = next(m for m in monitors if m['name'] == target)
    if internal.get('disabled') is not False or internal.get('width') != 1920 or internal.get('height') != 1080 or abs(internal.get('refreshRate', -999) - 60.061) > 0.15:
        raise Denied('mode drift')
    if not {'1920x1080@60.06Hz', '1920x1080@144.06Hz'}.issubset(set(internal.get('availableModes', []))):
        raise Denied('unadvertised mode')
    x, y = internal.get('x'), internal.get('y')
    if type(x) is not int or type(y) is not int or abs(x) > 32768 or abs(y) > 32768:
        raise Denied('position')
    for key, hz in (('candidateArgs', '144.06'), ('rollbackArgs', '60.06')):
        cmd = plan.get(key)
        if cmd != ['hyprctl', '-i', compositor['instance'], 'keyword', 'monitor', f'{target},1920x1080@{hz},{x}x{y},1']:
            raise Denied('command mismatch')
    # This is not a proof that a physical rollback worker is armed.
    # No value of watchdog grants physical execution here.
    return {'version':'0.6.0', 'decision':'PHYSICAL_EXECUTOR_PREFLIGHT_READONLY',
            'target':target, 'topology':capture['topology'], 'protected':'HDMI-A-1',
            'watchdogPhysicalVerified':False, 'ttyRecoveryVerified':False,
            'physicalExecutionEnabled':False, 'executionAuthorized':False,
            'commandsExecuted':0, 'rollbackPhysicalVerified':False}

def run_readonly(capture_path, plan_path, monitors_path, lua_path):
    def read_file(path):
        p = Path(path)
        if p.is_symlink() or not p.is_file() or p.stat().st_uid != os.getuid():
            raise Denied('unsafe input')
        return p.read_bytes()
    capture = json.loads(read_file(capture_path))
    plan = json.loads(read_file(plan_path))
    monitors = json.loads(read_file(monitors_path))
    lua = read_file(lua_path)
    instance = capture.get('compositor', {}).get('instance')
    if not isinstance(instance, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{8,160}', instance):
        raise Denied('invalid instance')
    env = {k:v for k,v in os.environ.items() if k not in ('WAYLAND_DISPLAY', 'DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE')}
    env['HYPRLAND_INSTANCE_SIGNATURE'] = instance
    def query(args):
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10, check=True)
        return json.loads(result.stdout)
    before = query(['hyprctl', 'instances', '-j'])
    live = query(['hyprctl', '-i', instance, 'monitors', '-j'])
    after = query(['hyprctl', 'instances', '-j'])
    if before != after:
        raise Denied('instance changed during capture')
    return check(plan, capture, monitors, lua, before, live)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--monitors', required=True)
    p.add_argument('--lua-file', required=True)
    args = p.parse_args()
    try:
        result = run_readonly(args.capture, args.plan, args.monitors, args.lua_file)
        code = 0
    except (Denied, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        result = {'decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,
                  'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0}
        code = 2
    print(json.dumps(result, indent=2))
    return code

if __name__ == '__main__':
    sys.exit(main())
