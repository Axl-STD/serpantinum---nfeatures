#!/usr/bin/env python3
"""v0.5.79.4: offline receipt interruption characterization, no systemd or display commands."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent

def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def safe_result(binding, token, meta, path):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open('r', encoding='utf-8') as f:
            receipt = json.load(f)
        return binding.assess(token, meta, receipt)['receiptMetadataConsistent'] is True
    except (OSError, ValueError, UnicodeError, TypeError, KeyError):
        return False

def child(stage, path, content):
    if stage == 'before-create':
        os._exit(71)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        if stage == 'after-create':
            os._exit(72)
        if stage == 'mid-write':
            out.write(content[:max(1, len(content)//2)])
            out.flush()
            os._exit(73)
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
        if stage == 'after-fsync':
            os._exit(74)
    return 0

def selftest():
    binding = load('display-worker-evidence-binding-v05791.py', 'binding_crash_05794')
    token = 'a'*24
    inv = '1'*32
    receipt = binding.worker_receipt(token, inv, 12345)
    content = binding.canonical(receipt)
    meta = dict(systemdExecutionMetadataConsistent=True, transactionId=token,
                invocationId=inv, execMainPid=12345, historicalIdentityVerified=False,
                executionAuthorized=False, physicalRollbackVerified=False)
    results = []
    def record(name, ok, **details):
        results.append(dict(scenario=name, passed=bool(ok), **details))
    with tempfile.TemporaryDirectory(prefix='serpantinum-v05794-') as root:
        os.chmod(root, 0o700)
        folder = Path(root)
        for stage, code, expected_present, expected_valid in [
            ('before-create',71,False,False), ('after-create',72,True,False),
            ('mid-write',73,True,False), ('after-fsync',74,True,True)]:
            path = folder / (stage+'.json')
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'child',
                                   '--stage', stage, '--path', str(path), '--content', content.hex()],
                                  capture_output=True, timeout=5)
            valid = safe_result(binding, token, meta, path)
            record(stage, proc.returncode == code and path.exists() == expected_present and valid == expected_valid,
                   exitCode=proc.returncode, filePresent=path.exists(), receiptAccepted=valid)
        path = folder/'valid.json'
        path.write_bytes(content)
        record('valid-receipt', safe_result(binding,token,meta,path))
        (folder/'truncated.json').write_bytes(content[:15])
        record('truncated-rejected', not safe_result(binding,token,meta,folder/'truncated.json'))
        (folder/'wrong-hash.json').write_text(json.dumps({**receipt,'sha256':'0'*64}))
        record('wrong-hash-rejected', not safe_result(binding,token,meta,folder/'wrong-hash.json'))
        target = folder/'target.json'
        target.write_bytes(content)
        link = folder/'symlink.json'
        link.symlink_to(target)
        record('symlink-rejected', not safe_result(binding,token,meta,link))
        exclusive = folder/'exclusive.json'
        exclusive.write_bytes(content)
        try:
            fd = os.open(exclusive, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            record('existing-receipt-preserved', exclusive.read_bytes()==content)
        else:
            os.close(fd)
            record('existing-receipt-preserved', False)
        concurrent = folder/'concurrent.json'
        command = [sys.executable, str(Path(__file__).resolve()), 'child', '--stage','normal',
                   '--path',str(concurrent),'--content',content.hex()]
        a = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        b = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        a.communicate(timeout=5)
        b.communicate(timeout=5)
        record('concurrent-exclusive-writer', sorted([a.returncode,b.returncode])==[0,1] and
               safe_result(binding,token,meta,concurrent), exitCodes=[a.returncode,b.returncode])
    passed = all(x['passed'] for x in results) and len(results)==10
    return dict(version='0.5.79.4', mode='offline-source-bound-receipt-crash-characterization',
                selftest='passed' if passed else 'failed', totalScenarios=len(results), results=results,
                directFinalPathWrite=True, atomicPublicationImplemented=False,
                crashDurabilityVerified=False, productionModified=False, systemdIntegration=False,
                historicalIdentityVerified=False, workerIdentityVerified=False,
                operationCompletionVerified=False, finalizationAuthorized=False,
                executionAuthorized=False, physicalExecutionEnabled=False,
                physicalRollbackVerified=False, commandsExecuted=0,
                limitations=['Process exit simulation, not power-loss durability testing',
                             'Child subprocess uses the audited exclusive-write pattern; production is not modified',
                             'Receipt SHA256 does not authenticate the local writer'])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['selftest','child'])
    parser.add_argument('--stage')
    parser.add_argument('--path')
    parser.add_argument('--content')
    args=parser.parse_args()
    if args.command=='child':
        return child(args.stage, args.path, bytes.fromhex(args.content))
    result=selftest()
    print(json.dumps(result,indent=2))
    return 0 if result['selftest']=='passed' else 2

if __name__=='__main__':
    sys.exit(main())
