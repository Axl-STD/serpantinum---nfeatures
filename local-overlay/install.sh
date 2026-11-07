#!/usr/bin/env bash
# Installs the local display manager overlay (Settings > Display > Monitors) so that it
# survives Serpantinum updates.
#
#   local-overlay/install.sh              install / update the overlay and apply it now
#   local-overlay/install.sh --uninstall  remove it (Serpantinum's own pages are untouched)
#
# The payload lives OUTSIDE the Serpantinum install (an update or reinstall wipes that
# folder), in ~/.local/share/serpantinum-overlay/display-manager. A systemd user path unit
# watches the files an update rewrites and runs apply.sh, which puts the overlay back.
set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
PAYLOAD="$DATA/serpantinum-overlay/display-manager"
TARGET="$DATA/serpantinum"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN="$HOME/.local/bin"
NAME=serpantinum-display-overlay

have_systemd() { command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    if have_systemd; then
        systemctl --user disable --now "$NAME.path" "$NAME.service" 2>/dev/null || true
    fi
    rm -f "$UNITS/$NAME.path" "$UNITS/$NAME.service" "$BIN/serpantinum-display"
    have_systemd && systemctl --user daemon-reload || true
    popup="$TARGET/src/quickshell/guide/GuidePopup.qml"
    if [[ -f "$popup" ]] && grep -q 'displaymgr/DisplayMainTab.qml' "$popup"; then
        cp -a "$popup" "$popup.bak-overlay-uninstall"
        sed -i '/displaymgr\/DisplayMainTab\.qml/d' "$popup"
        echo "menu entry removed (backup: $popup.bak-overlay-uninstall)"
    fi
    rm -rf "$TARGET/src/quickshell/guide/displaymgr" "$PAYLOAD"
    echo "Overlay removed. Added language keys are left in place (unused and harmless)."
    exit 0
fi

src="$REPO/src/quickshell/guide/displaymgr"
[[ -f "$src/display_txn.py" ]] || { echo "not a serpantinum overlay checkout: $REPO" >&2; exit 1; }

# 1. Payload (atomic swap so the path unit never sees a half-copied payload).
mkdir -p "$(dirname "$PAYLOAD")"
tmp="$PAYLOAD.new"
rm -rf "$tmp"
mkdir -p "$tmp"
cp -a "$src" "$tmp/displaymgr"
find "$tmp" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 "$REPO/local-overlay/apply.sh" "$tmp/apply.sh"
install -m 755 "$REPO/local-overlay/serpantinum-display" "$tmp/serpantinum-display"
git -C "$REPO" rev-parse --short HEAD > "$tmp/VERSION" 2>/dev/null || echo unknown > "$tmp/VERSION"
rm -rf "$PAYLOAD"
mv "$tmp" "$PAYLOAD"
echo "payload: $PAYLOAD ($(cat "$PAYLOAD/VERSION"))"

# 2. CLI.
mkdir -p "$BIN"
ln -sfn "$PAYLOAD/serpantinum-display" "$BIN/serpantinum-display"
echo "cli: $BIN/serpantinum-display"

# 3. Automatic re-apply after updates.
if have_systemd; then
    mkdir -p "$UNITS"
    install -m 644 "$REPO/local-overlay/systemd/$NAME.path" "$UNITS/$NAME.path"
    install -m 644 "$REPO/local-overlay/systemd/$NAME.service" "$UNITS/$NAME.service"
    systemctl --user daemon-reload
    systemctl --user enable --now "$NAME.path" >/dev/null
    systemctl --user enable "$NAME.service" >/dev/null
    echo "systemd: $NAME.path watching for Serpantinum updates"
else
    echo "systemd user session not available: run 'serpantinum-display reapply' after each update."
fi

# 4. Apply now.
"$PAYLOAD/apply.sh"
