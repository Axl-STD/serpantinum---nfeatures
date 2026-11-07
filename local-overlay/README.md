# Local display manager overlay

Personal overlay: **Settings → Display → Monitors**, a display manager for Hyprland, Sway,
niri and Mango (arrangement map, keep-or-revert, profiles, opt-in boot persistence). It is not
part of upstream Serpantinum, so it is kept apart from upstream files and re-applied after
every update.

## Install / update / remove

    local-overlay/install.sh              # install or update, then apply now
    local-overlay/install.sh --uninstall  # remove; Serpantinum's own pages stay as they were

## How it survives updates

- Code: `src/quickshell/guide/displaymgr/` (upstream has no such folder) and its strings in
  `displaymgr/strings/<lang>.json`.
- Payload copy: `~/.local/share/serpantinum-overlay/display-manager/` (outside the install
  folder, which a reinstall wipes).
- `apply.sh` (idempotent) copies the module, adds missing language keys and one subtab line in
  `GuidePopup.qml`. It backs up every file first (`~/.local/state/serpantinum-overlay/backups`,
  last 5) and logs to `~/.local/state/serpantinum-overlay/display-manager.log`.
- `serpantinum-display-overlay.path` (systemd user) runs it when an update rewrites
  `GuidePopup.qml` or the languages; the service also runs once per login.
- If upstream changes the Display subtabs so the anchor is not found, the menu line is not
  added, the error is logged and a notification is shown.

## CLI (works from a TTY)

    serpantinum-display rescue|status|revert|confirm|reapply|log

## Boot persistence lines (add them yourself, once)

| Compositor | Line |
|---|---|
| Hyprland | `require("serpantinum/monitors")` in `hyprland.lua` |
| Sway | `include ~/.config/sway/serpantinum/monitors` at the end of the config |
| niri | `include "serpantinum/monitors.kdl"` at the end of `config.kdl` |
| Mango | `source_optional=./serpantinum/monitors.conf` as the first **and** last line |

## Keeping the branch current

    git fetch upstream && git rebase upstream/master   # only displaymgr/, tests and local-overlay/ are ours
    python3 -m unittest discover -s tests/display && local-overlay/install.sh
