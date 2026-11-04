# Serpantinum Display Manager v0.6.0

## Development checkpoint
- Branch: feature/display-admission-v0600
- Base commit: d20a31a (v0.5.93)
- Status: development laboratory, not production-ready.
- Physical execution: disabled.
- Physical watchdog armed: false.
- Physical rollback verified: false.

## Environment
- Arch Linux, Hyprland, Quickshell 0.3.2.
- Internal display: eDP-1, 1920x1080, 60.061 Hz.
- Available alternative: 144.06 Hz.
- Settings entrypoint: src/quickshell/SettingsApp.qml.
- Settings must use isolated temporary configuration and cache.
- Active desktop installation must remain untouched.

## Validated milestones
- IPC transaction and backend isolation regressions passed.
- Shared persistent transport regressions passed.
- SIGKILL recovery and systemd automatic restart passed in simulation.
- Physical Watchdog Runtime Lab: 22/22 PASS.
- Physical Supervisor Admission: 30/30 PASS.
- Physical Supervisor Shadow: 24/24 PASS.
- Physical Obligation Observer: 31/31 PASS.
- Recovery Service Lab: 11/11 PASS.
- Recovery Service systemd terminal E2E: PASS.
- Active Recovery Service systemd E2E: PASS.
- Evidence UX Fix: 11/11 PASS.
- Settings translations and evidence UX visually confirmed.

## Pending
- Recovery Service Resilience E2E systemd validation.
- Physical watchdog obligation and service integration.
- Restricted real Hyprland rollback executor.
- Independent TTY3 physical recovery verification.
- Controlled physical refresh-rate trial.
- Final Settings confirmation and rollback integration.

## Safety invariants
- ARMED_LAB_ONLY never authorizes physical execution.
- Simulated recovery is not physical rollback evidence.
- Preserve transaction identity, snapshot integrity and IPC locking.
- Never claim physical rollback without fresh verification.
- Keep Lua and the active desktop installation intact.

## Resume instructions
Review this document, the current branch, and the latest test results.
Continue from Recovery Service Resilience E2E and physical watchdog integration.
Do not repeat already validated laboratory regressions unnecessarily.
Use consolidated functional test blocks and preserve safe checkpoints.
