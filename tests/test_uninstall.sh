#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script="$repo_dir/uninstall.sh"

if [[ $EUID -ne 0 ]]; then
  echo "uninstall shell tests skipped: system-service test requires root"
  exit 0
fi

new_case() {
  case_dir="$(mktemp -d)"
  mkdir -p "$case_dir/bin" "$case_dir/project/.venv" "$case_dir/project/.wmadapter-profile"
  mkdir -p "$case_dir/home/.local/share/wmadapter/profiles/deepseek" "$case_dir/home/.local/share/wmadapter/profiles/qwen"
  cp "$script" "$case_dir/project/uninstall.sh"
  chmod +x "$case_dir/project/uninstall.sh"
  touch "$case_dir/project/config.yaml" "$case_dir/project/keep.me"
export XDG_CONFIG_HOME="$case_dir/config" HOME="$case_dir/home" WMADAPTER_SYSTEMD_DIR="$case_dir/systemd" WMADAPTER_UNINSTALL_HOME="$case_dir/home"
mkdir -p "$WMADAPTER_SYSTEMD_DIR"
  mkdir -p "$XDG_CONFIG_HOME" "$HOME"
}

assert_removed() {
  [[ ! -e "$case_dir/project/.venv" && ! -e "$case_dir/project/.wmadapter-profile" ]]
  [[ ! -e "$case_dir/home/.local/share/wmadapter/profiles" ]]
  [[ ! -e "$case_dir/project/config.yaml" ]]
  [[ -e "$case_dir/project/keep.me" ]]
}

new_case
cat >"$case_dir/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$SYSTEMCTL_LOG"
exit 0
EOF
chmod +x "$case_dir/bin/systemctl"
export SYSTEMCTL_LOG="$case_dir/systemctl.log"
PATH="$case_dir/bin:/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" >/dev/null
assert_removed
PATH="$case_dir/bin:/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" >/dev/null
assert_removed
if [[ $EUID -eq 0 ]]; then
  grep -q -- 'disable --now wmadapter.service' "$SYSTEMCTL_LOG"
fi

new_case
cat >"$case_dir/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
sleep 30
EOF
chmod +x "$case_dir/bin/systemctl"
output=$(PATH="$case_dir/bin:/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" 2>&1)
if [[ $EUID -eq 0 ]]; then
  grep -q 'systemd service stop/disable timed out' <<<"$output"
  grep -q 'systemd daemon-reload timed out' <<<"$output"
fi
assert_removed

new_case
cat >"$case_dir/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 23
EOF
chmod +x "$case_dir/bin/systemctl"
output=$(PATH="$case_dir/bin:/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" 2>&1)
if [[ $EUID -eq 0 ]]; then
  grep -q 'systemd service stop/disable failed (exit 23)' <<<"$output"
fi
assert_removed

new_case
output=$(PATH="/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" 2>&1)
assert_removed

echo "uninstall shell tests passed"
