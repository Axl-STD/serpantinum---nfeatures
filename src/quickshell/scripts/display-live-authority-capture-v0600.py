#!/usr/bin/env python3
"""Fail-closed live Hyprland identity capture; read-only, never an execution token."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

class CaptureDenied(RuntimeError):
    pass

def starttime(pid, proc='/proc'):
    raw=(Path(proc)/str(pid)/'stat').read_text()
    tail=raw[raw.rfind(')')+2:].split()
    if len(tail)<20: raise CaptureDenied('invalid process stat')
    return int(tail[19])

def identity(instances, runtime, uid, proc='/proc'):
    if not isinstance(instances,list) or len(instances)!=1: raise CaptureDenied('ambiguous instances')
    item=instances[0]
    if not isinstance(item,dict): raise CaptureDenied('invalid instance')
    pid=item.get('pid'); sig=item.get('instance')
    if type(pid) is not int or pid<2 or not isinstance(sig,str) or not sig or '/' in sig or sig in ('.','..'):
        raise CaptureDenied('invalid identity')
    p=Path(proc)/str(pid)
    if p.stat().st_uid!=uid: raise CaptureDenied('foreign compositor')
    folder=Path(runtime)/'hypr'/sig
    s=folder.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid or stat.S_IMODE(s.st_mode)!=0o700: raise CaptureDenied('unsafe instance directory')
    sock=(folder/'.socket.sock').lstat()
    if not stat.S_ISSOCK(sock.st_mode) or sock.st_uid!=uid: raise CaptureDenied('unsafe socket')
    return {'pid':pid,'starttime':starttime(pid,proc),'instance':sig,'socketDevice':sock.st_dev,'socketInode':sock.st_ino}

def fingerprint(monitors):
    if not isinstance(monitors,list) or len(monitors)!=2: raise CaptureDenied('unexpected monitor count')
    names=[m.get('name') for m in monitors if isinstance(m,dict)]
    if len(names)!=2 or len(set(names))!=2 or 'HDMI-A-1' not in names or len(set(names)&{'eDP-1','eDP-2'})!=1:
        raise CaptureDenied('unexpected topology')
    return hashlib.sha256(json.dumps(sorted(monitors,key=lambda m:m['name']),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def capture(hypr_dir,lua_backup,config_backup,runtime=None,uid=None,runner=subprocess.run):
    uid=os.getuid() if uid is None else uid
    runtime=Path(runtime or os.environ.get('XDG_RUNTIME_DIR','/run/user/'+str(uid)))
    rs=runtime.lstat()
    if not stat.S_ISDIR(rs.st_mode) or rs.st_uid!=uid: raise CaptureDenied('unsafe runtime')
    env=dict(os.environ);env['XDG_RUNTIME_DIR']=str(runtime)
    env.pop('HYPRLAND_INSTANCE_SIGNATURE',None);env.pop('WAYLAND_DISPLAY',None)
    def query(args,environment):
        p=runner(args,env=environment,capture_output=True,text=True,timeout=10,check=True)
        return json.loads(p.stdout)
    first=query(['hyprctl','instances','-j'],env)
    before=identity(first,runtime,uid)
    env['HYPRLAND_INSTANCE_SIGNATURE']=before['instance']
    monitors_before=query(['hyprctl','monitors','-j'],env)
    fp_before=fingerprint(monitors_before)
    source=Path(hypr_dir)/'config/monitors.lua'
    lua_before=source.read_bytes()
    plan_script=Path(__file__).resolve().parent/'display-dynamic-monitor-plan-v0600.py'
    if plan_script.is_symlink() or not plan_script.is_file(): raise CaptureDenied('missing planner')
    plan=query([sys.executable,'-B',str(plan_script),'--hypr-dir',str(hypr_dir),'--lua-backup',str(lua_backup),'--config-backup',str(config_backup)],env)
    second=query(['hyprctl','instances','-j'],env)
    after=identity(second,runtime,uid)
    monitors_after=query(['hyprctl','monitors','-j'],env)
    if before!=after or fp_before!=fingerprint(monitors_after) or lua_before!=source.read_bytes():
        raise CaptureDenied('capture changed')
    if plan.get('decision')!='DYNAMIC_READ_ONLY_PLAN_READY' or plan.get('target') not in ('eDP-1','eDP-2') or plan.get('protected')!='HDMI-A-1' or plan.get('physicalExecutionEnabled') is not False or plan.get('executionAuthorized') is not False or type(plan.get('commandsExecuted')) is not int or plan['commandsExecuted']!=0:
        raise CaptureDenied('planner rejected')
    if plan['target'] not in [m['name'] for m in monitors_after]: raise CaptureDenied('connector changed')
    if plan.get('luaSha256')!=hashlib.sha256(lua_before).hexdigest(): raise CaptureDenied('Lua digest mismatch')
    return {'version':'0.6.0','decision':'LIVE_AUTHORITY_READONLY_READY','compositor':before,'target':plan['target'],'alternate':plan['alternate'],'protected':'HDMI-A-1','persistentSource':'config/monitors.lua','luaSha256':plan['luaSha256'],'monitorDigest':fp_before,'captureAtomic':False,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0,'rollbackPhysicalVerified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--hypr-dir',required=True);p.add_argument('--lua-backup',required=True);p.add_argument('--config-backup',required=True)
    a=p.parse_args()
    try: result=capture(a.hypr_dir,a.lua_backup,a.config_backup);rc=0
    except (OSError,ValueError,TypeError,CaptureDenied,subprocess.SubprocessError) as exc:
        result={'version':'0.6.0','decision':'BLOCK_AND_INVESTIGATE','errorType':type(exc).__name__,'physicalExecutionEnabled':False,'executionAuthorized':False,'commandsExecuted':0};rc=2
    print(json.dumps(result,indent=2));return rc
if __name__=='__main__':sys.exit(main())
