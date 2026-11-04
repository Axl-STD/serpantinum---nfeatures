#!/usr/bin/env python3
"""Read-only TTY recovery readiness. NEVER issues monitor mutations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

class PreflightDenied(RuntimeError):
    pass

def load_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise PreflightDenied('invalid JSON') from exc

def safe_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PreflightDenied('unsafe file')
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_size > 1024 * 1024:
        raise PreflightDenied('unsafe file metadata')
    return path.read_bytes()

def validate(instances, monitors, backup, config, socket_ok, tty, require_tty=True):
    if require_tty and tty != '/dev/tty2':
        raise PreflightDenied('not tty2')
    if not isinstance(instances, list) or len(instances) != 1:
        raise PreflightDenied('ambiguous compositor')
    instance = instances[0]
    if not isinstance(instance, dict) or not isinstance(instance.get('instance'), str) or not instance['instance'] or '/' in instance['instance'] or not isinstance(instance.get('pid'), int) or instance['pid'] <= 0:
        raise PreflightDenied('invalid compositor identity')
    if not socket_ok:
        raise PreflightDenied('missing control socket')
    if not isinstance(monitors, list) or len(monitors) != 2:
        raise PreflightDenied('monitor topology changed')
    names = [m.get('name') for m in monitors if isinstance(m, dict)]
    if len(names) != 2 or set(names) != {'eDP-1', 'HDMI-A-1'}:
        raise PreflightDenied('unexpected monitors')
    if not isinstance(backup, list) or len(backup) != 2:
        raise PreflightDenied('invalid backup topology')
    by_name = {m['name']: m for m in monitors}
    saved = {m['name']: m for m in backup if isinstance(m, dict) and 'name' in m}
    if set(saved) != set(by_name):
        raise PreflightDenied('backup topology mismatch')
    for name in by_name:
        now, old = by_name[name], saved[name]
        for key in ('width', 'height', 'scale', 'transform', 'x', 'y', 'disabled'):
            if now.get(key) != old.get(key):
                raise PreflightDenied('monitor state differs: ' + name + '/' + key)
        if abs(float(now.get('refreshRate', -100)) - float(old.get('refreshRate', -200))) > 0.05:
            raise PreflightDenied('refresh differs: ' + name)
    internal = by_name['eDP-1']
    modes = internal.get('availableModes')
    if not isinstance(modes, list) or '1920x1080@60.06Hz' not in modes or '1920x1080@144.06Hz' not in modes:
        raise PreflightDenied('required mode missing')
    if internal['disabled'] or by_name['HDMI-A-1']['disabled']:
        raise PreflightDenied('monitor disabled')
    return {'version':'0.6.0', 'mode':'tty-readonly-preflight', 'decision':'READ_ONLY_READY', 'tty':tty,
            'compositorPid':instance['pid'], 'monitors':names, 'configSha256':hashlib.sha256(config).hexdigest(),
            'physicalExecutionEnabled':False, 'executionAuthorized':False, 'commandsExecuted':0}

def run(args):
    backup_dir = Path(args.backup_dir).expanduser()
    config = safe_file(backup_dir / 'monitors.conf')
    saved_hash = safe_file(backup_dir / 'monitors.sha256').decode().strip().split()[0]
    if saved_hash != hashlib.sha256(config).hexdigest():
        raise PreflightDenied('backup checksum mismatch')
    backup = load_json(safe_file(backup_dir / 'monitors.json'))
    if args.fixtures:
        root = Path(args.fixtures)
        instances = load_json(safe_file(root / 'instances.json'))
        monitors = load_json(safe_file(root / 'monitors.json'))
        socket_ok = (root / 'socket-present').is_file()
        tty = args.tty_override or '/dev/tty2'
    else:
        tty = os.ttyname(sys.stdin.fileno()) if sys.stdin.isatty() else 'NOT_A_TTY'
        env = os.environ.copy()
        env.pop('HYPRLAND_INSTANCE_SIGNATURE', None)
        env.pop('WAYLAND_DISPLAY', None)
        env['XDG_RUNTIME_DIR'] = '/run/user/' + str(os.getuid())
        def query(*argv):
            p = subprocess.run(['hyprctl', *argv], env=env, capture_output=True, text=True, timeout=8, check=True)
            return load_json(p.stdout)
        instances = query('instances', '-j')
        if not isinstance(instances, list) or len(instances) != 1:
            raise PreflightDenied('ambiguous compositor')
        sig = instances[0].get('instance', '')
        if not isinstance(sig, str) or not sig or '/' in sig:
            raise PreflightDenied('invalid instance')
        socket_path = Path(env['XDG_RUNTIME_DIR']) / 'hypr' / sig / '.socket.sock'
        socket_ok = socket_path.is_socket() and not socket_path.is_symlink()
        env['HYPRLAND_INSTANCE_SIGNATURE'] = sig
        p = subprocess.run(['hyprctl', 'monitors', '-j'], env=env, capture_output=True, text=True, timeout=8, check=True)
        monitors = load_json(p.stdout)
    return validate(instances, monitors, backup, config, socket_ok, tty, require_tty=not args.allow_graphical)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--backup-dir', required=True)
    p.add_argument('--fixtures', help='offline test only')
    p.add_argument('--tty-override', help='offline test only')
    p.add_argument('--allow-graphical', action='store_true', help='read-only diagnostics only')
    args = p.parse_args()
    if args.tty_override and not args.fixtures:
        p.error('tty override only allowed with fixtures')
    try:
        print(json.dumps(run(args), indent=2))
        return 0
    except (PreflightDenied, OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        print(json.dumps({'decision':'BLOCK_AND_INVESTIGATE', 'errorType':type(exc).__name__,
                          'error':str(exc), 'physicalExecutionEnabled':False, 'commandsExecuted':0}, indent=2))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
