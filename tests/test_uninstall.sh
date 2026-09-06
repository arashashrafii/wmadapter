#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script="$repo_dir/uninstall.sh"

new_case() {
  case_dir="$(mktemp -d)"
  mkdir -p "$case_dir/bin" "$case_dir/project/.venv" "$case_dir/project/.mimicgate-profile"
  cp "$script" "$case_dir/project/uninstall.sh"
  chmod +x "$case_dir/project/uninstall.sh"
  touch "$case_dir/project/config.yaml" "$case_dir/project/keep.me"
  export XDG_CONFIG_HOME="$case_dir/config" HOME="$case_dir/home"
  mkdir -p "$XDG_CONFIG_HOME" "$HOME"
}

assert_removed() {
  [[ ! -e "$case_dir/project/.venv" && ! -e "$case_dir/project/.mimicgate-profile" ]]
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
grep -q -- '--user disable --now mimicgate.service' "$SYSTEMCTL_LOG"

new_case
cat >"$case_dir/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
sleep 30
EOF
chmod +x "$case_dir/bin/systemctl"
output=$(PATH="$case_dir/bin:/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" 2>&1)
grep -q 'systemd service stop/disable timed out' <<<"$output"
grep -q 'systemd daemon-reload timed out' <<<"$output"
assert_removed

new_case
cat >"$case_dir/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 23
EOF
chmod +x "$case_dir/bin/systemctl"
output=$(PATH="$case_dir/bin:/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" 2>&1)
grep -q 'systemd service stop/disable failed (exit 23)' <<<"$output"
assert_removed

new_case
output=$(PATH="/usr/bin:/bin" UNINSTALL_TIMEOUT_SEC=1 "$case_dir/project/uninstall.sh" 2>&1)
assert_removed

echo "uninstall shell tests passed"
