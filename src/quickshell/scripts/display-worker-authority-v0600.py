#!/usr/bin/env python3
"""Offline-only worker handshake. Does not confer display execution authority.

A coordinator admits one directly spawned worker by kernel SO_PEERCRED PID,
its /proc starttime, and an allowlisted operation. The worker is NOT a lock
owner. A successful handshake is not a persistent authorization or rollback.
"""
import contextlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import time

class AuthorityError(RuntimeError):
    pass

def starttime(pid):
    if type(pid) is not int or pid <= 0:
        raise AuthorityError('invalid pid')
    raw = Path('/proc') / str(pid) / 'stat'
    try:
        data = raw.read_text()
        suffix = data[data.rfind(')') + 2:].split()
        return int(suffix[19])  # field 22 (index 19 after state field 3)
    except (OSError, ValueError, IndexError) as exc:
        raise AuthorityError('process identity unavailable') from exc

class Broker:
    def __init__(self, root, operations=('offline-probe',)):
        self.root = Path(root)
        self.operations = frozenset(operations)
        self.allowed = {}
        self.sock = None
        self.path = self.root / 'worker.sock'
        self.closed = False
        self._socket_identity = None

    def __enter__(self):
        s = self.root.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
            raise AuthorityError('unsafe root')
        if os.path.lexists(self.path):
            raise AuthorityError('socket path occupied')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.path))
            os.chmod(self.path, 0o600)
            sock.listen(8)
            sock.settimeout(3)
        except BaseException:
            sock.close()
            raise
        st = self.path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            sock.close()
            raise AuthorityError('unsafe bound socket')
        self._socket_identity = (st.st_dev, st.st_ino)
        self.sock = sock
        return self

    def register(self, pid, operation):
        if self.closed or self.sock is None:
            raise AuthorityError('broker inactive')
        if operation not in self.operations:
            raise AuthorityError('operation not allowed')
        if pid in self.allowed:
            raise AuthorityError('duplicate registration')
        self.allowed[pid] = (starttime(pid), operation)

    def serve_once(self):
        if self.closed or self.sock is None:
            raise AuthorityError('broker inactive')
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(2)
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            pid, uid, _gid = struct.unpack('3i', creds)
            raw = conn.recv(256)
            try:
                msg = json.loads(raw)
                op = msg['operation']
                if type(op) is not str or set(msg) != {'operation'}:
                    raise ValueError('invalid request')
            except (ValueError, KeyError, TypeError):
                op = None
            registered = self.allowed.pop(pid, None)
            ok = (uid == os.getuid() and registered is not None and
                  registered[1] == op and op in self.operations)
            if ok:
                try:
                    ok = starttime(pid) == registered[0]
                except AuthorityError:
                    ok = False
            conn.sendall(b'ACK\n' if ok else b'DENY\n')
            return {'accepted': bool(ok), 'peerPid': pid, 'operation': op}

    def __exit__(self, *_):
        self.closed = True
        self.allowed.clear()
        if self.sock is not None:
            self.sock.close()
        try:
            st = self.path.lstat()
            if (stat.S_ISSOCK(st.st_mode) and self._socket_identity is not None
                    and (st.st_dev, st.st_ino) == self._socket_identity):
                self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._socket_identity = None

def request(path, operation):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect(str(path))
        sock.sendall(json.dumps({'operation': operation}).encode())
        return sock.recv(32) == b'ACK\n'
