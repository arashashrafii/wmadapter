#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="webbridgefreeride.service"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}"

echo "Uninstalling WebBridge FreeRide local service..."
if command -v openclaw >/dev/null 2>&1; then
  openclaw plugins disable webbridgefreeride-openclaw >/dev/null 2>&1 || true
  openclaw plugins uninstall webbridgefreeride-openclaw >/dev/null 2>&1 || true
fi
systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SERVICE_FILE"
systemctl --user daemon-reload 2>/dev/null || true

rm -rf "$PROJECT_DIR/.venv" "$PROJECT_DIR/.webbridge-profile"
rm -f \
  "$PROJECT_DIR/.env" \
  "$PROJECT_DIR/config.yaml" \
  "$PROJECT_DIR/.webbridgefreeride.pid" \
  "$PROJECT_DIR/webbridgefreeride.install.log" \
  "$PROJECT_DIR/webbridgefreeride.log"

echo "WebBridge FreeRide was uninstalled. Source files were kept in: $PROJECT_DIR"
