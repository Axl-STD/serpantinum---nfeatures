#!/usr/bin/env python3
"""v0.5.90: adversarial, no-systemd regression of the consolidated gate."""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('security_gate_v0590', HERE / 'display-evidence-security-gate-v0590.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def selftest():
    cases = []
    def record(name, condition):
        assert condition, name
        cases.append({'scenario': name, 'passed': True})
    def fixture(label='historical-evidence', runtime=False):
        count = 5 if runtime else 2
        rows = [{'component': 'part-'+str(i), 'passed': True, 'scenarios': 1,
                 'evidenceScope': ('real-systemd-runtime' if runtime and i >= 2 else 'offline-selftest-only')}
                for i in range(count)]
        return {'mode': 'runtime' if runtime else 'offline', 'selftest': 'passed',
                'totalScenarios': count, 'results': rows, 'systemdIntegration': runtime,
                'productionModified': False, 'automaticCleanup': False,
                'executionAuthorized': False, 'physicalExecutionEnabled': False,
                'physicalRollbackVerified': False, 'atomicCrossStoreSnapshot': False,
                'commandsExecuted': 0}
    def accepted(data, label='historical-evidence', runtime=False):
        try:
            gate.validate(label, data, 5 if runtime else (4 if label == 'resource-identity' else 2), runtime)
            return True
        except ValueError:
            return False
    base = fixture()
    record('valid-offline', accepted(base))
    for name, mutation in (
        ('missing-results', lambda d: d.pop('results')),
        ('empty-results', lambda d: d.update(results=[])),
        ('boolean-count', lambda d: d.update(totalScenarios=True)),
        ('float-count', lambda d: d.update(totalScenarios=2.0)),
        ('wrong-count', lambda d: d.update(totalScenarios=3)),
        ('failed-row', lambda d: d['results'][0].update(passed=False)),
        ('nonboolean-passed', lambda d: d['results'][0].update(passed=1)),
        ('missing-component', lambda d: d['results'][0].pop('component')),
        ('missing-scope', lambda d: d['results'][0].pop('evidenceScope')),
        ('wrong-scope', lambda d: d['results'][0].update(evidenceScope='real-systemd-runtime')),
        ('boolean-commands', lambda d: d.update(commandsExecuted=False)),
        ('nonzero-commands', lambda d: d.update(commandsExecuted=1)),
        ('physical-overclaim', lambda d: d.update(physicalExecutionEnabled=True)),
        ('cleanup-overclaim', lambda d: d.update(automaticCleanup=True)),
        ('atomic-overclaim', lambda d: d.update(atomicCrossStoreSnapshot=True)),
        ('missing-authorization', lambda d: d.pop('executionAuthorized')),
        ('runtime-overclaim', lambda d: d.update(systemdIntegration=True)),
        ('wrong-mode', lambda d: d.update(mode='runtime')),
        ('failed-selftest', lambda d: d.update(selftest='failed')),
    ):
        candidate = fixture()
        mutation(candidate)
        record(name, not accepted(candidate))
    real = fixture(runtime=True)
    record('valid-runtime', accepted(real, runtime=True))
    changed = fixture(runtime=True)
    changed['results'][-1]['evidenceScope'] = 'offline-selftest-only'
    record('runtime-scope-mismatch', not accepted(changed, runtime=True))
    class Completed:
        def __init__(self, code=0, payload='{}'):
            self.returncode, self.stdout = code, payload
    calls = []
    def fake_runner(argv):
        calls.append(argv[-1])
        return Completed(2, '{}')
    try:
        gate.execute('runtime', fake_runner)
    except ValueError:
        pass
    else:
        raise AssertionError('failed to stop')
    record('stop-first-failure', calls == ['offline'])
    record('runtime-opt-in', len(gate.OFFLINE) == 2 and len(gate.RUNTIME) == 2)
    record('no-global-lock-claim', 'no global cross-gate lock' in gate.report('runtime', [])['limitations'][0])
    record('report-no-authorization', gate.report('runtime', [])['executionAuthorized'] is False)
    record('report-no-ownership', gate.report('runtime', [])['resourceOwnershipVerified'] is False)
    record('report-no-physical', gate.report('runtime', [])['physicalExecutionEnabled'] is False)
    record('offline-no-systemd', gate.report('offline', [])['systemdIntegration'] is False)
    return {'version': '0.5.90', 'mode': 'selftest', 'selftest': 'passed',
            'totalScenarios': len(cases), 'results': cases, 'systemdIntegration': False,
            **{k: False for k in gate.FALSE_FLAGS}, 'commandsExecuted': 0}


def main():
    if len(sys.argv) != 2 or sys.argv[1] != 'selftest':
        return 2
    print(json.dumps(selftest(), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
