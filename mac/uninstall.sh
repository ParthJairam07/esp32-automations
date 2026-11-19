#!/bin/bash
# Stop the listener. The saved shortcut name is left in place.
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.parthjairam.esp32-shortcut.plist"
LABEL="com.parthjairam.esp32-shortcut"
UID_NUM="$(id -u)"

launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootout "gui/${UID_NUM}" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

echo "Stopped the listener."
echo "The shortcut name is still saved in:"
echo "  $HOME/Library/Application Support/esp32-automations/config.json"
