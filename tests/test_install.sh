#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script="$repo_dir/install.sh"

bash -n "$script"
grep -q 'MIMICGATE_LOGIN=1 .venv/bin/mimicgate auth' "$script"
grep -q 'interactive_session_unavailable' "$script"
grep -q 'Environment=WAYLAND_DISPLAY=' "$script"
grep -q 'Environment=XAUTHORITY=' "$script"
grep -q 'Environment=DBUS_SESSION_BUS_ADDRESS=' "$script"
grep -q 'cd -- "$PROJECT_DIR"' "$script"
grep -q 'ensure_browser' "$script"
grep -q 'executable_path: ${executable_path}' "$script"
grep -q 'trap cleanup_failed_install EXIT' "$script"
! grep -q 'playwright install chromium' "$script"

auth_line=$(grep -n 'run_foreground_auth' "$script" | tail -1 | cut -d: -f1)
start_line=$(grep -n '^start_service 0$' "$script" | tail -1 | cut -d: -f1)
stop_line=$(grep -n '^stop_service$' "$script" | tail -1 | cut -d: -f1)
ensure_line=$(grep -n '^ensure_browser$' "$script" | tail -1 | cut -d: -f1)
config_line=$(grep -n '^write_config "\$PROVIDER"' "$script" | head -1 | cut -d: -f1)
[[ "$stop_line" -lt "$auth_line" && "$auth_line" -lt "$start_line" ]]
[[ "$ensure_line" -lt "$config_line" ]]

echo "install orchestration tests passed"
