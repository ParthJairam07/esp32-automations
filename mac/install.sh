#!/bin/bash
# Install the listener so it starts at login and runs one Shortcut.
set -euo pipefail

# The button runs this Shortcut. Pass another name to override it.
SHORTCUT="${1:-Daddy}"

if [[ -z "${SHORTCUT//[[:space:]]/}" ]]; then
  echo 'Usage: ./mac/install.sh ["Shortcut Name"]'
  echo 'With no name, the button runs "Daddy".'
  exit 1
fi

if ! command -v shortcuts >/dev/null 2>&1; then
  echo "This Mac does not have the shortcuts command. It comes with macOS Monterey and later."
  exit 1
fi

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "python3 is required."
  echo "Install it with: brew install python"
  echo "Or download it from https://www.python.org/downloads/macos/"
  exit 1
fi

APP_DIR="$HOME/Library/Application Support/esp32-automations"
LOG_DIR="$HOME/Library/Logs"
AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENT_DIR/com.parthjairam.esp32-shortcut.plist"
LABEL="com.parthjairam.esp32-shortcut"
SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/esp32_shortcut_listener.py"
SCRIPT_DST="$APP_DIR/esp32_shortcut_listener.py"
LOG_FILE="$LOG_DIR/esp32-automations.log"

mkdir -p "$APP_DIR" "$LOG_DIR" "$AGENT_DIR"
cp "$SCRIPT_SRC" "$SCRIPT_DST"
chmod 755 "$SCRIPT_DST"

"$PYTHON" - "$APP_DIR/config.json" "$SHORTCUT" <<'PY'
import json
import pathlib
import sys

path, shortcut = sys.argv[1], sys.argv[2]
dest = pathlib.Path(path)
existing = {}
if dest.exists():
    try:
        loaded = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        loaded = {}
    if isinstance(loaded, dict):
        existing = loaded
existing["shortcut"] = shortcut
existing.setdefault("confirm_sound", True)
dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
PY

"$PYTHON" - "$PYTHON" "$SCRIPT_DST" "$LOG_FILE" "$PLIST" <<'PY'
import pathlib
import plistlib
import sys

python, script, log_path, plist_path = sys.argv[1:]
payload = {
    "Label": "com.parthjairam.esp32-shortcut",
    "ProgramArguments": [python, script],
    "RunAtLoad": True,
    "KeepAlive": True,
    "LimitLoadToSessionType": "Aqua",
    "ProcessType": "Interactive",
    "StandardOutPath": log_path,
    "StandardErrorPath": log_path,
}
pathlib.Path(plist_path).write_bytes(plistlib.dumps(payload))
PY
chmod 644 "$PLIST"

UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootout "gui/${UID_NUM}" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "$PLIST"
launchctl enable "gui/${UID_NUM}/${LABEL}" || true
launchctl kickstart -k "gui/${UID_NUM}/${LABEL}"

echo "Installed. The BOOT button runs: ${SHORTCUT}"
echo "Log: ${LOG_FILE}"
echo "Check the cable with:"
echo "  \"$PYTHON\" \"$SCRIPT_DST\" --doctor"
