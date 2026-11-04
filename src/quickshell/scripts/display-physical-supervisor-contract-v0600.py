#!/usr/bin/env python3
"""Read-only physical supervisor admission. Never invokes monitor-changing commands."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent

class SupervisorDenied(RuntimeError):
    pass

def load(name, alias):
    p = HERE / name
    if p.is_symlink() or not p.is_file():
        raise SupervisorDenied('unsafe dependency')
    spec = importlib.util.spec_from_file_location(alias, p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def check_instance(instances, runtime, uid):
    if not isinstance(instances, list) or len(instances) != 1:
        raise SupervisorDenied('ambiguous compositor instances')
    item = instances[0]
    if not isinstance(item, dict):
        raise SupervisorDenied('invalid instance')
    pid = item.get('pid')
    signature = item.get('instance')
    if type(pid) is not int or pid < 2 or not isinstance(signature, str) or not signature or '/' in signature or signature in ('.', '..'):
        raise SupervisorDenied('invalid compositor identity')
    proc = Path('/proc') / str(pid)
    if not proc.exists() or proc.stat().st_uid != uid:
        raise SupervisorDenied('compositor owner unavailable')
    folder = runtime / 'hypr' / signature
    if folder.is_symlink() or not folder.is_dir():
        raise SupervisorDenied('missing compositor directory')
    info = folder.stat()
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise SupervisorDenied('unsafe compositor directory')
    socket = folder / '.socket.sock'
    if socket.is_symlink() or not socket.exists() or not stat.S_ISSOCK(socket.stat().st_mode) or socket.stat().st_uid != uid:
        raise SupervisorDenied('unsafe compositor socket')
    return signature, pid

def validate_readonly(gate, instances, runtime, uid, allow_tty=False):
    if gate.get('decision') != 'DRY_RUN_READY' or gate.get('physicalExecutionEnabled') is not False or gate.get('commandsExecuted') != 0:
        raise SupervisorDenied('physical gate rejected')
    signature, pid = check_instance(instances, runtime, uid)
    return {'version':'0.6.0','mode':'physical-supervisor-readonly','decision':'SUPERVISOR_READONLY_READY',
            'compositorPid':pid,'instance':signature,'monitor':'eDP-1','untouched':'HDMI-A-1',
            'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,
            'rollbackPhysicalVerified':False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup-dir', required=True)
    args = parser.parse_args()
    try:
        gate_script = HERE / 'display-physical-executor-dryrun-v0600.py'
        if gate_script.is_symlink() or not gate_script.is_file():
            raise SupervisorDenied('missing physical gate')
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/'+str(os.getuid())))
        if runtime.is_symlink() or not runtime.is_dir() or runtime.stat().st_uid != os.getuid():
            raise SupervisorDenied('unsafe runtime')
        env = dict(os.environ)
        env['XDG_RUNTIME_DIR'] = str(runtime)
        env.pop('HYPRLAND_INSTANCE_SIGNATURE', None)
        env.pop('WAYLAND_DISPLAY', None)
        inst = subprocess.run(['hyprctl','instances','-j'],env=env,capture_output=True,text=True,timeout=8)
        if inst.returncode:
            raise SupervisorDenied('instance discovery failed')
        instances = json.loads(inst.stdout)
        signature, pid = check_instance(instances, runtime, os.getuid())
        env['HYPRLAND_INSTANCE_SIGNATURE'] = signature
        gate = subprocess.run([sys.executable,'-B',str(gate_script),'--backup-dir',args.backup_dir],env=env,capture_output=True,text=True,timeout=15)
        if gate.returncode:
            raise SupervisorDenied('physical gate failed: '+gate.stdout[:400])
        output = validate_readonly(json.loads(gate.stdout), instances, runtime, os.getuid())
        print(json.dumps(output,indent=2)); return 0
    except (OSError,ValueError,TypeError,SupervisorDenied,subprocess.SubprocessError) as exc:
        print(json.dumps({'version':'0.6.0','decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,
                          'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0})); return 2
if __name__ == '__main__':
    sys.exit(main())
