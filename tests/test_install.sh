#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script="$repo_dir/install.sh"

bash -n "$script"
! grep -q 'run_foreground_auth' "$script"
! grep -q 'WMADAPTER_LOGIN=1' "$script"
! grep -q 'wait_service_ready' "$script"
! grep -q 'run_smoke' "$script"
grep -q 'Environment=WAYLAND_DISPLAY=' "$script"
grep -q 'Environment=XAUTHORITY=' "$script"
grep -q 'Environment=DBUS_SESSION_BUS_ADDRESS=' "$script"
grep -q 'cd -- "$PROJECT_DIR"' "$script"
grep -q 'ensure_browser' "$script"
grep -q 'executable_path: ${executable_path}' "$script"
grep -q 'trap cleanup_failed_install EXIT' "$script"
! grep -q 'playwright install chromium' "$script"
grep -q 'Google Chrome was not found' "$script"
grep -q 'system Google Chrome executable' "$script"
grep -q 'xvfb-run' "$script"
grep -q 'WMADAPTER_XVFB=' "$script"
grep -q 'Xvfb is required' "$script"
grep -q -- "--server-args='-screen 0 1440x1000x24'" "$script"
ensure_line=$(grep -n '^ensure_browser$' "$script" | tail -1 | cut -d: -f1)
config_line=$(grep -n '^write_config "\$BROWSER_EXECUTABLE"' "$script" | head -1 | cut -d: -f1)
service_line=$(grep -n '^write_service 0$' "$script" | tail -1 | cut -d: -f1)
[[ "$ensure_line" -lt "$config_line" ]]
[[ "$config_line" -lt "$service_line" ]]
grep -q 'provider list' "$script"
grep -q 'profiles were preserved' "$script"
grep -q 'chown -- "\$TARGET_USER:\$TARGET_GROUP" config.yaml' "$script"
grep -q 'WMADAPTER_INSTALL_MARKER' "$script"
grep -q 'CLI_FILE="\${BIN_DIR}/wmadapter"' "$script"
grep -q 'say "  wmadapter provider list"' "$script"
! grep -q 'rm -rf -- "\$profile_root"' "$script"

echo "install orchestration tests passed"
