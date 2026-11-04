#!/usr/bin/env python3
"""Offline IPC transaction laboratory. NEVER invokes hyprctl or changes displays.

The live command proposal is data, not authority. A standalone simulated
monitor is the only backend; physical execution is intentionally absent.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time

BASE = '1920x1080@60.06'
TARGET = '1920x1080@144.06'
STATES = {'PREPARED', 'ARMED', 'APPLIED', 'UNCERTAIN', 'ROLLBACK_REQUIRED', 'ROLLED_BACK', 'CONFIRMED'}

class Denied(RuntimeError):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'

def root_check(root):
    p = Path(root)
    if not p.is_absolute() or p.is_symlink() or not p.is_dir():
        raise Denied('unsafe root')
    st = p.stat()
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise Denied('unsafe root owner or permissions')
    return p

@contextlib.contextmanager
def locked(root):
    p = root_check(root)
    fd = os.open(p / '.ipc-lab.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
            raise Denied('unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield p
    finally:
        os.close(fd)

def read(root, filename):
    path = root / filename
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 16384:
            raise Denied('unsafe record')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return json.loads(stream.read(16385))
    finally:
        os.close(fd)

def write(root, filename, value):
    data = canonical(value)
    fd, temp = tempfile.mkstemp(prefix='.ipc-lab-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, root / filename)
        dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('decision') != 'DYNAMIC_TOPOLOGY_COMMANDS_READONLY':
        raise Denied('invalid plan')
    if plan.get('physicalExecutionEnabled') is not False or plan.get('executionAuthorized') is not False or type(plan.get('commandsExecuted')) is not int or plan['commandsExecuted'] != 0:
        raise Denied('plan claims physical authority')
    target = plan.get('target')
    if target not in ('eDP-1', 'eDP-2') or plan.get('protected') != 'HDMI-A-1' or plan.get('topology') not in ('internal-only', 'internal-and-hdmi'):
        raise Denied('invalid topology')
    for key, hz in (('candidateArgs', TARGET), ('rollbackArgs', BASE)):
        cmd = plan.get(key)
        if not isinstance(cmd, list) or len(cmd) != 6 or cmd[:1] != ['hyprctl'] or cmd[3:5] != ['keyword', 'monitor']:
            # Expected actual shape: hyprctl -i INSTANCE keyword monitor SPEC (6 entries)
            raise Denied('invalid command shape')
        if cmd[1] != '-i' or not isinstance(cmd[2], str) or not cmd[2]:
            raise Denied('invalid command')
        if not isinstance(cmd[5], str) or not cmd[5].startswith(target + ',' + hz + ','):
            raise Denied('invalid mode')
    if plan['candidateArgs'][2] != plan['rollbackArgs'][2]:
        raise Denied('different compositor')
    return target

def prepare(root, plan, deadline_boottime_ns, boot_id):
    target = validate_plan(plan)
    if type(deadline_boottime_ns) is not int or deadline_boottime_ns <= time.clock_gettime_ns(time.CLOCK_BOOTTIME):
        raise Denied('invalid deadline')
    if not isinstance(boot_id, str) or not boot_id:
        raise Denied('invalid boot id')
    with locked(root) as p:
        if (p / 'transaction.json').exists() or (p / 'simulated-ipc.json').exists():
            raise Denied('existing transaction')
        token = secrets.token_hex(32)
        record = {'schema': 1, 'id': token, 'generation': 1, 'state': 'PREPARED', 'target': target,
                  'topology': plan['topology'], 'plan_sha256': hashlib.sha256(canonical(plan)).hexdigest(),
                  'boot_id': boot_id, 'deadline_boottime_ns': deadline_boottime_ns, 'phase': 'NONE'}
        write(p, 'simulated-ipc.json', {'connector': target, 'mode': BASE})
        write(p, 'transaction.json', record)
        return record

def action(root, token, operation, now_ns=None, boot_id=None, failpoint=None):
    now_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_ns is None else now_ns
    with locked(root) as p:
        r = read(p, 'transaction.json')
        observed = read(p, 'simulated-ipc.json')
        if r.get('id') != token or observed.get('connector') != r.get('target'):
            raise Denied('identity changed')
        if boot_id is not None and r['boot_id'] != boot_id:
            raise Denied('boot changed')
        if operation == 'arm':
            if r['state'] != 'PREPARED' or now_ns >= r['deadline_boottime_ns']:
                raise Denied('cannot arm')
            r.update(state='ARMED', generation=r['generation'] + 1)
        elif operation == 'apply':
            if r['state'] != 'ARMED' or observed['mode'] != BASE or now_ns >= r['deadline_boottime_ns']:
                raise Denied('cannot apply')
            r.update(state='UNCERTAIN', phase='APPLY_INTENT', generation=r['generation'] + 1)
            write(p, 'transaction.json', r)
            if failpoint == 'after-intent': return r
            write(p, 'simulated-ipc.json', {'connector': r['target'], 'mode': TARGET})
            if failpoint == 'after-effect': return r
            r.update(state='APPLIED', phase='APPLY_VERIFIED', generation=r['generation'] + 1)
        elif operation == 'rollback':
            if r['state'] not in ('APPLIED', 'ROLLBACK_REQUIRED') or observed['mode'] not in (BASE, TARGET):
                raise Denied('cannot rollback')
            r.update(state='UNCERTAIN', phase='ROLLBACK_INTENT', generation=r['generation'] + 1)
            write(p, 'transaction.json', r)
            if failpoint == 'after-intent': return r
            write(p, 'simulated-ipc.json', {'connector': r['target'], 'mode': BASE})
            if failpoint == 'after-effect': return r
            r.update(state='ROLLED_BACK', phase='COMPLETED', generation=r['generation'] + 1)
        elif operation == 'expire':
            if r['state'] != 'APPLIED' or now_ns < r['deadline_boottime_ns']:
                raise Denied('not due')
            r.update(state='ROLLBACK_REQUIRED', generation=r['generation'] + 1)
        elif operation == 'inspect':
            return {'record': r, 'observed': observed, 'physicalExecutionEnabled': False, 'commandsExecuted': 0}
        else:
            raise Denied('unknown operation')
        write(p, 'transaction.json', r)
        return r

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root')
    ap.add_argument('--inspect', action='store_true')
    args = ap.parse_args()
    try:
        if not args.inspect: raise Denied('only inspection exposed by CLI')
        result = action(args.root, read(root_check(args.root), 'transaction.json')['id'], 'inspect')
        result['decision'] = 'SIMULATED_IPC_INSPECTED'
        rc = 0
    except (OSError, ValueError, TypeError, KeyError, Denied) as exc:
        result = {'decision': 'BLOCK_AND_INVESTIGATE', 'errorType': type(exc).__name__, 'physicalExecutionEnabled': False, 'commandsExecuted': 0}
        rc = 2
    print(json.dumps(result, indent=2))
    return rc
if __name__ == '__main__': raise SystemExit(main())
