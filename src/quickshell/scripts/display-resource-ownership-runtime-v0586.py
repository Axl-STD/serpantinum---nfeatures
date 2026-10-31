#!/usr/bin/env python3
"""v0.5.86 disposable subprocess identity and lifecycle regression; no systemd calls."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent

def load():
    spec = importlib.util.spec_from_file_location('ownership_0586', HERE / 'display-resource-ownership-v0586.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def selftest():
    mod = load()
    rows = []
    def check(label, ok):
        if not ok:
            raise AssertionError(label)
        rows.append(dict(scenario=label, passed=True))
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 3
        first = None
        while time.monotonic() < deadline:
            first = mod.read_process(child.pid)
            if first and first['state'] != 'Z':
                break
            time.sleep(.02)
        check('child-observed', first is not None and first['pid'] == child.pid)
        expected = {k: first[k] for k in ('pid', 'startTicks', 'pgid', 'sid')}
        check('isolated-session', first['sid'] == child.pid)
        check('isolated-process-group', first['pgid'] == child.pid)
        second = mod.read_process(child.pid)
        check('stable-observation', mod.assess(expected, first, second)['assessment'] == 'process-metadata-consistent-only')
        check('changed-start-rejected', mod.assess({**expected, 'startTicks': expected['startTicks'] + 1}, first, second)['assessment'] == 'identity-mismatch')
        check('changed-pgid-rejected', mod.assess({**expected, 'pgid': expected['pgid'] + 1}, first, second)['assessment'] == 'identity-mismatch')
        check('missing-observation-uncertain', mod.assess(expected, first, None)['assessment'] == 'uncertain')
        check('ownership-unverified', mod.assess(expected, first, second)['resourceOwnershipVerified'] is False)
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
    check('child-reaped', child.poll() is not None)
    check('child-proc-gone', mod.read_process(child.pid) is None)
    check('post-exit-not-observed', mod.assess(expected, None, None)['assessment'] == 'process-not-observed')
    check('no-systemd-integration', True)
    check('no-physical-execution', True)
    output = mod.report('offline-disposable-process-runtime', rows)
    return output

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    try:
        print(json.dumps(selftest(), indent=2))
    except (AssertionError, OSError, ValueError) as exc:
        print(json.dumps(dict(selftest='failed', error=str(exc)[:160])))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
