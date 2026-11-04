#!/usr/bin/env python3
"""Offline schema-2 fenced intent journal. Isolated from schema-1 production APIs.

Not an authorization boundary: caller must hold owner and serialization locks.
Never migrates schema-1, reclaims journals, or operates displays.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

SCHEMA = 2
PHASES = {'prepared','applying','awaiting_confirmation','confirmed','rollback_required','rolled_back','uncertain'}
EDGES = {'prepared': {'applying','rollback_required'},
         'applying': {'awaiting_confirmation','rollback_required','uncertain'},
         'awaiting_confirmation': {'confirmed','rollback_required','uncertain'},
         'rollback_required': {'rolled_back','uncertain'},
         'confirmed': set(), 'rolled_back': set(), 'uncertain': set()}
FIELDS = {'schema','generation','phase','operation','snapshot_digest','session_id','coordinator_identity','recovery_required'}
class FenceDenied(RuntimeError): pass

def _canonical(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')

def _root(root):
    p=Path(root)
    if not p.is_absolute() or p.is_symlink() or not p.is_dir(): raise FenceDenied('unsafe root')
    s=p.stat()
    if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700: raise FenceDenied('unsafe root permissions')
    return p

def _identity():
    pid=os.getpid()
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        start=int(fields[19])
    except (OSError,ValueError,IndexError) as e: raise FenceDenied('coordinator identity unavailable') from e
    return f'{os.getuid()}:{pid}:{start}'

def _validate(p):
    if type(p) is not dict or set(p)!=FIELDS or p['schema']!=SCHEMA: raise FenceDenied('unsupported journal schema')
    if type(p['generation']) is not int or p['generation']<1 or p['phase'] not in PHASES: raise FenceDenied('invalid journal generation or phase')
    if type(p['recovery_required']) is not bool: raise FenceDenied('invalid recovery flag')
    for key in ('operation','snapshot_digest','coordinator_identity'):
        if type(p[key]) is not str or not 0<len(p[key])<=128: raise FenceDenied('invalid journal identity')
    sid=p['session_id']
    if type(sid) is not str or len(sid)!=64 or any(c not in '0123456789abcdef' for c in sid): raise FenceDenied('invalid session id')
    return p

def read(root):
    p=_root(root)/'intent.json'
    if p.is_symlink(): raise FenceDenied('symlink journal')
    try: fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    except FileNotFoundError: return None
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1 or st.st_size>16384: raise FenceDenied('unsafe journal')
        with os.fdopen(fd,'rb',closefd=False) as f: data=f.read(16385)
        if len(data)>16384: raise FenceDenied('oversized journal')
        try:
            obj=json.loads(data)
            if type(obj) is not dict or set(obj)!={'payload','sha256'}: raise ValueError('wrapper')
            payload=_validate(obj['payload'])
            if obj['sha256']!=hashlib.sha256(_canonical(payload)).hexdigest(): raise ValueError('checksum')
            return payload
        except (ValueError,TypeError,KeyError,UnicodeError) as e: raise FenceDenied('corrupt or legacy journal') from e
    finally: os.close(fd)

def _write(root,p):
    root=_root(root)
    _validate(p)
    if (root/'intent.json').is_symlink(): raise FenceDenied('symlink journal')
    data=_canonical({'payload':p,'sha256':hashlib.sha256(_canonical(p)).hexdigest()})+b'\n'
    fd,name=tempfile.mkstemp(prefix='.intent-fence-',dir=root)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb',closefd=False) as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,root/'intent.json')
        dfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        os.close(fd)
        if os.path.exists(name): os.unlink(name)

def begin(root,operation,digest):
    if any(type(v) is not str or not 0<len(v)<=128 for v in (operation,digest)): raise FenceDenied('invalid operation')
    if read(root) is not None: raise FenceDenied('existing journal requires external reconciliation')
    p={'schema':SCHEMA,'generation':1,'phase':'prepared','operation':operation,
       'snapshot_digest':digest,'session_id':secrets.token_hex(32),
       'coordinator_identity':_identity(),'recovery_required':True}
    _write(root,p)
    return p

def advance(root,session_id,generation,phase):
    p=read(root)
    if p is None: raise FenceDenied('missing journal')
    if type(session_id) is not str or not secrets.compare_digest(p['session_id'],session_id): raise FenceDenied('foreign session')
    if p['coordinator_identity']!=_identity(): raise FenceDenied('foreign coordinator incarnation')
    if type(generation) is not int or p['generation']!=generation: raise FenceDenied('stale generation')
    if phase not in EDGES[p['phase']]: raise FenceDenied('invalid transition')
    nextp=dict(p,generation=generation+1,phase=phase,recovery_required=True)
    _write(root,nextp)
    return nextp

def decision(root):
    try: p=read(root)
    except (FenceDenied,OSError): return 'BLOCK_AND_INVESTIGATE'
    if p is None: return 'NO_JOURNAL_NOT_PROOF_OF_SAFETY'
    if p['phase'] in ('confirmed','rolled_back'): return 'TERMINAL_JOURNAL_REQUIRES_EXTERNAL_VERIFICATION'
    return 'BLOCK_AND_RECONCILE_EXTERNAL_STATE'
