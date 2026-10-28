#!/bin/sh
# Install (or reinstall) the launchd agent that renders the day's videos automatically.
# Usage: sh demo/launchd/install.sh          uninstall: sh demo/launchd/install.sh --uninstall
set -e
LABEL=com.tintuc.autorender
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "--uninstall" ]; then rm -f "$PLIST"; echo "Đã gỡ $LABEL"; exit 0; fi
NODE_DIR="$(dirname "$(command -v node)")"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/output"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT/.venv-demo/bin/python</string>
    <string>$PROJECT/demo/auto_render.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$NODE_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$PROJECT/output/auto-render.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/output/auto-render.log</string>
</dict>
</plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Đã cài $LABEL: kiểm tra Sheet mỗi 10 phút trong khung giờ auto_render.window của demo/config.json"
