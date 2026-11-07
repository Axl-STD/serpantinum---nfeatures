#!/usr/bin/env bash
# Re-applies the local display manager overlay onto an installed Serpantinum.
#
# Idempotent: it only writes what is missing or different, so running it again (by hand,
# at login, or from the path unit after an update) changes nothing when all is in place.
# It touches exactly three things inside the install:
#   1. src/quickshell/guide/displaymgr/          (our module; upstream has no such dir)
#   2. src/assets/languages/*.json               (adds OUR keys only when missing)
#   3. src/quickshell/guide/GuidePopup.qml       (one subtab line under Display)
# Every file it is about to change is backed up first. Nothing else is ever touched.
set -euo pipefail

PAYLOAD="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
TARGET="${SERPANTINUM_TARGET:-$HOME/.local/share/serpantinum}"
STATE="${SERPANTINUM_OVERLAY_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/serpantinum-overlay}"
LOG="$STATE/display-manager.log"
QUIET=false
[[ "${1:-}" == "--quiet" ]] && QUIET=true

mkdir -p "$STATE"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; $QUIET || echo "$*"; }
fail() {
    log "ERROR: $*"
    command -v notify-send >/dev/null && notify-send -a Serpantinum "Display manager overlay" "$*" 2>/dev/null || true
    exit 1
}

QS="$TARGET/src/quickshell"
POPUP="$QS/guide/GuidePopup.qml"
LANGS="$TARGET/src/assets/languages"

if [[ ! -f "$POPUP" || ! -d "$LANGS" ]]; then
    # Install missing or mid-update: the path unit fires again when the files appear.
    log "Serpantinum not (fully) installed at $TARGET; nothing to do."
    exit 0
fi
[[ -d "$PAYLOAD/displaymgr" ]] || fail "payload missing: $PAYLOAD/displaymgr"

BACKUP="$STATE/backups/$(date +%Y%m%d-%H%M%S)"
backup() {  # backup <absolute path inside TARGET>
    local rel="${1#"$TARGET"/}"
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp -a "$1" "$BACKUP/$rel"
}

changed=0

# 1. Module directory.
if ! diff -rq --exclude=__pycache__ "$PAYLOAD/displaymgr" "$QS/guide/displaymgr" >/dev/null 2>&1; then
    [[ -d "$QS/guide/displaymgr" ]] && backup "$QS/guide/displaymgr"
    tmp="$QS/guide/.displaymgr.new"
    rm -rf "$tmp"
    cp -a "$PAYLOAD/displaymgr" "$tmp"
    find "$tmp" -name __pycache__ -prune -exec rm -rf {} +
    chmod +x "$tmp/display_txn.py"
    rm -rf "$QS/guide/displaymgr"
    mv "$tmp" "$QS/guide/displaymgr"
    log "module: installed guide/displaymgr"
    changed=1
fi

# 2 + 3. Language keys and the menu entry (python: JSON merge and exact text edit).
set +e
py_out=$(python3 -I - "$PAYLOAD/displaymgr/strings" "$LANGS" "$POPUP" "$TARGET" "$BACKUP" 2>&1 <<'PY'
import json, os, re, shutil, sys

strings_dir, langs_dir, popup, target, backup = sys.argv[1:6]

def save_backup(path):
    rel = os.path.relpath(path, target)
    dest = os.path.join(backup, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(path, dest)

def write_atomic(path, text):
    tmp = path + ".overlay-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)

def merge_missing(dst, src):
    """Adds keys from src that dst lacks; never overwrites an upstream value."""
    added = 0
    for k, v in src.items():
        if isinstance(v, dict):
            if k not in dst:
                dst[k] = {}
            if isinstance(dst[k], dict):
                added += merge_missing(dst[k], v)
        elif k not in dst:
            dst[k] = v
            added += 1
    return added

for name in sorted(os.listdir(strings_dir)):
    if not name.endswith(".json"):
        continue
    lang_file = os.path.join(langs_dir, name)
    if not os.path.isfile(lang_file):
        continue  # upstream dropped this language; nothing to merge into
    with open(os.path.join(strings_dir, name), encoding="utf-8") as f:
        ours = json.load(f)
    with open(lang_file, encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
    except ValueError:
        print(f"skip {name}: upstream file is not valid JSON", file=sys.stderr)
        continue
    n = merge_missing(data, ours)
    if n:
        save_backup(lang_file)
        write_atomic(lang_file, json.dumps(data, ensure_ascii=False, indent=4) + "\n")
        json.load(open(lang_file, encoding="utf-8"))  # verify
        print(f"languages: {name} +{n} keys")
        changed = True

MARK = 'file: "displaymgr/DisplayMainTab.qml"'
ENTRY = '{ id: "DisplayMonitors", key: "display_monitors", name: "Monitors", icon: "󰍺", file: "displaymgr/DisplayMainTab.qml" }'
with open(popup, encoding="utf-8") as f:
    src = f.read()
if MARK not in src:
    # Anchor: the last subtab of the Display tab (upstream: the Lock screen subtab).
    m = re.search(r'^([ \t]*)(\{[^{}\n]*id:\s*"DisplayLockscreen"[^{}\n]*\})[ \t]*,?[ \t]*$', src, re.M)
    if not m:
        print("menu: Display subtabs not found in GuidePopup.qml (upstream changed it); "
              "menu entry NOT added. The module is installed; update local-overlay/apply.sh.",
              file=sys.stderr)
        sys.exit(1)
    indent, item = m.group(1), m.group(2)
    new = f"{indent}{item},\n{indent}{ENTRY}"
    src2 = src[:m.start()] + new + src[m.end():]
    save_backup(popup)
    write_atomic(popup, src2)
    with open(popup, encoding="utf-8") as f:
        if MARK not in f.read():
            sys.exit("menu: verification failed")
    print("menu: added Display > Monitors")
    changed = True

PY
)
rc=$?
set -e
[[ -n "$py_out" ]] && while IFS= read -r line; do log "$line"; done <<< "$py_out"
[[ $rc -eq 0 ]] || fail "overlay only partly applied (see $LOG)."
[[ -n "$py_out" ]] && changed=1

if [[ $changed -eq 1 ]]; then
    log "applied (backup in $BACKUP)"
else
    log "already up to date"
fi
# Keep the 5 newest backups.
ls -1dt "$STATE/backups"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
