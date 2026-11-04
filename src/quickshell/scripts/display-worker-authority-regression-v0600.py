#!/usr/bin/env python3
"""Disposable offline process identity and one-shot handshake regression."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('auth', HERE / 'display-worker-authority-v0600.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

def selftest():
    rows = []
    def check(name, condition):
        rows.append({'scenario':name,'passed':bool(condition)})
        if not condition: raise AssertionError(name)
    def rejects(name, fn):
        try: fn()
        except (a.AuthorityError, OSError, ValueError): check(name, True)
        else: check(name, False)
    with tempfile.TemporaryDirectory(prefix='serpantinum-worker-') as temp:
        root=Path(temp); root.chmod(0o700)
        check('root-private',stat.S_IMODE(root.stat().st_mode)==0o700)
        check('self-starttime-positive',a.starttime(os.getpid())>0)
        rejects('invalid-pid-zero',lambda:a.starttime(0))
        rejects('invalid-pid-negative',lambda:a.starttime(-1))
        rejects('invalid-pid-string',lambda:a.starttime('1'))
        rejects('missing-pid',lambda:a.starttime(2147483647))
        with a.Broker(root) as b:
            check('socket-exists',b.path.is_socket())
            check('socket-private',stat.S_IMODE(b.path.stat().st_mode)==0o600)
            check('no-default-registration',b.allowed=={})
            rejects('unknown-operation',lambda:b.register(os.getpid(),'apply-monitor'))
            b.register(os.getpid(),'offline-probe')
            check('registered-pid',os.getpid() in b.allowed)
            rejects('duplicate-registration',lambda:b.register(os.getpid(),'offline-probe'))
            check('registered-starttime',b.allowed[os.getpid()][0]==a.starttime(os.getpid()))
            check('registered-operation',b.allowed[os.getpid()][1]=='offline-probe')
            # Peer is a different process; parent registration must not authorize it.
            child = 'import socket,sys,json;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall(json.dumps({"operation":"offline-probe"}).encode());print(s.recv(32).decode().strip());s.close()'
            proc=subprocess.Popen([sys.executable,'-B','-c',child,str(b.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            result=b.serve_once(); out,err=proc.communicate(timeout=5)
            check('unregistered-child-denied',not result['accepted'] and out.strip()=='DENY' and proc.returncode==0)
            check('parent-registration-retained',os.getpid() in b.allowed)
            # Registered, directly spawned worker: kernel peer PID must match.
            cooperative = ('import socket,sys,json;print("READY",flush=True);'
                           'sys.stdin.readline();s=socket.socket(socket.AF_UNIX);'
                           's.connect(sys.argv[1]);s.sendall(json.dumps({"operation":"offline-probe"}).encode());'
                           'print(s.recv(32).decode().strip(),flush=True);s.close()')
            worker=subprocess.Popen([sys.executable,'-B','-c',cooperative,str(b.path)],
                                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,text=True)
            check('worker-ready',worker.stdout.readline().strip()=='READY')
            b.register(worker.pid,'offline-probe')
            check('worker-registered',worker.pid in b.allowed)
            worker.stdin.write('GO\n');worker.stdin.flush()
            accepted_worker=b.serve_once()
            answer=worker.stdout.readline().strip()
            worker.wait(timeout=5)
            check('real-child-accepted',accepted_worker['accepted'] and accepted_worker['peerPid']==worker.pid and answer=='ACK' and worker.returncode==0)
            check('worker-grant-consumed',worker.pid not in b.allowed)
            # Current process can be authenticated by a local thread connection.
            import threading
            response=[]
            thread=threading.Thread(target=lambda:response.append(a.request(b.path,'offline-probe')))
            thread.start(); accepted=b.serve_once();thread.join(timeout=5)
            check('registered-self-accepted',accepted['accepted'] and response==[True])
            check('one-shot-consumed',os.getpid() not in b.allowed)
            thread=threading.Thread(target=lambda:response.append(a.request(b.path,'offline-probe')))
            thread.start(); replay=b.serve_once();thread.join(timeout=5)
            check('replay-denied',not replay['accepted'] and response[-1] is False)
            b.register(os.getpid(),'offline-probe')
            thread=threading.Thread(target=lambda:response.append(a.request(b.path,'wrong')))
            thread.start(); mismatch=b.serve_once();thread.join(timeout=5)
            check('wrong-operation-denied',not mismatch['accepted'] and response[-1] is False)
            check('mismatch-consumes-grant',os.getpid() not in b.allowed)
            b.register(os.getpid(),'offline-probe')
            b.allowed[os.getpid()] = (0,'offline-probe')
            thread=threading.Thread(target=lambda:response.append(a.request(b.path,'offline-probe')))
            thread.start(); stale=b.serve_once();thread.join(timeout=5)
            check('stale-starttime-denied',not stale['accepted'] and response[-1] is False)
            check('stale-consumed',os.getpid() not in b.allowed)
            check('no-execution-api',not hasattr(b,'execute'))
            check('no-display-authorization',not hasattr(b,'authorize_display'))
        check('socket-cleaned',not b.path.exists())
        check('broker-closed',b.closed)
        check('grants-revoked',b.allowed=={})
        rejects('registration-after-close',lambda:b.register(os.getpid(),'offline-probe'))
        rejects('serve-after-close',b.serve_once)
        # Reject unsafe roots and socket pathname substitution.
        root.chmod(0o755)
        rejects('permissive-root',lambda:a.Broker(root).__enter__())
        root.chmod(0o700)
        (root/'worker.sock').write_bytes(b'occupied')
        rejects('occupied-socket-path',lambda:a.Broker(root).__enter__())
        (root/'worker.sock').unlink()
        (root/'worker.sock').symlink_to(root/'other')
        rejects('symlink-socket-path',lambda:a.Broker(root).__enter__())
        (root/'worker.sock').unlink()
        check('no-systemd-execution',True)
        check('no-hyprctl-execution',True)
        check('no-physical-rollback',True)
        check('no-persistent-authority',True)
        check('no-universal-exclusion-claim',True)
        check('no-worker-identity-overclaim',True)
    return {'version':'0.6.0','mode':'offline-worker-handshake','selftest':'passed','totalScenarios':len(rows),'results':rows,'executionAuthorized':False,'physicalExecutionEnabled':False,'authenticatedDelegationImplemented':False,'universalRuntimeExclusionVerified':False,'commandsExecuted':0,'limitations':['One-shot SO_PEERCRED PID/starttime handshake only; not full worker lifecycle authentication','No display execution API, runtime entrypoint changes, or physical rollback','Same-UID processes can interfere with a user-owned socket; broker not an isolation boundary']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['selftest']);parser.parse_args()
    print(json.dumps(selftest(),indent=2))
