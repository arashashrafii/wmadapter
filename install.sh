#!/usr/bin/env bash
set -euo pipefail

API_HOST="127.0.0.1"
API_PORT="11555"
REPO_URL="https://github.com/arashashrafii/wmadapter"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${PROJECT_DIR}/$(basename -- "${BASH_SOURCE[0]}")"
SERVICE_NAME="wmadapter.service"
SERVICE_DIR="/etc/systemd/system"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}"
BIN_DIR="${WMADAPTER_BIN_DIR:-/usr/local/bin}"
CLI_FILE="${BIN_DIR}/wmadapter"
DISPLAY_VALUE="${DISPLAY:-}"
BROWSER_EXECUTABLE=""
INSTALL_SUCCESS=0
USE_XVFB=0

cd -- "$PROJECT_DIR"

if [[ $EUID -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "This installer needs root privileges, but sudo is not installed. Run it as root." >&2
    exit 1
  fi
  echo "Root privileges are required for the system-wide service; requesting them once..."
  exec sudo --preserve-env=DISPLAY,WAYLAND_DISPLAY,XAUTHORITY,DBUS_SESSION_BUS_ADDRESS,XDG_RUNTIME_DIR -- "$SCRIPT_PATH" "$@"
fi

# The unit is system-wide, but browser automation must run as the desktop user
# so Chrome can use the user's display and its normal sandbox.
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
TARGET_USER="${TARGET_USER:-root}"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_HOME="${TARGET_HOME:-/root}"

say() { printf '\n%s\n' "$*"; }
ask() {
  local prompt="$1" default="${2:-}"
  local answer
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " answer || true
    printf '%s' "${answer:-$default}"
  else
    read -r -p "$prompt: " answer || true
    printf '%s' "$answer"
  fi
}
port_available() {
  ! (echo >"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}
find_free_port() {
  local port="$1"
  while ! port_available "$port"; do
    port=$((port + 1))
    [ "$port" -le 65535 ] || { echo "No free port found" >&2; return 1; }
  done
  printf "%s" "$port"
}
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}
detect_browser() {
  local candidate version program_files_x86
  program_files_x86="$(printenv 'PROGRAMFILES(X86)' 2>/dev/null || true)"
  local candidates=("google-chrome" "google-chrome-stable" "google-chrome-beta"
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    "${PROGRAMFILES:-}/Google/Chrome/Application/chrome.exe"
    "${program_files_x86}/Google/Chrome/Application/chrome.exe"
    "${LOCALAPPDATA:-}/Google/Chrome/Application/chrome.exe")
  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
      version="$($candidate --version 2>/dev/null || true)"
      if printf '%s' "$version" | grep -Eq 'Google Chrome|Chrome'; then
        if command -v "$candidate" >/dev/null 2>&1; then command -v "$candidate"; else printf '%s' "$candidate"; fi
        return 0
      fi
    fi
  done
  return 1
}
resolve_browser_path() {
  local requested="$1"
  case "${requested,,}" in
    googlechrome) requested="google-chrome" ;;
  esac
  if [ -x "$requested" ]; then
    printf '%s' "$requested"
  elif command -v "$requested" >/dev/null 2>&1; then
    command -v "$requested"
  else
    return 1
  fi
}
write_config() {
  local executable_path="$1" server_host="$2"
  cat > config.yaml <<YAML
server:
  host: ${server_host}
  port: ${API_PORT}

browser:
  mode: managed
  headless: true
  profile_dir: ${TARGET_HOME}/.local/share/wmadapter/profiles/deepseek
  executable_path: ${executable_path}
  cdp_endpoint: null
  restart_retries: 1

qwen:
  chat_url: https://chat.qwen.ai/auth
  auth: google
  profile_dir: ${TARGET_HOME}/.local/share/wmadapter/profiles/qwen
  headless: true
  models:
    - qwen-chat
  image_generation_verified: false

deepseek:
  transport: web
  chat_url: https://chat.deepseek.com/
  timeout_ms: 180000
  login_timeout_ms: 30000
  system_prompt: Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required.

providers:
  default: deepseek
  enabled:
    - deepseek
  enabled_models:
    - deepseek-chat

limits:
  context_budget_chars: 24000

logging:
  level: INFO
  file: ${TARGET_HOME}/.local/share/wmadapter/wmadapter.log
  max_bytes: 1000000
  backup_count: 3
YAML
}
install_current_os() {
  need python3
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -e '.[test]'
}
ensure_browser() {
  BROWSER_EXECUTABLE="$(detect_browser || true)"
  if [ -z "$BROWSER_EXECUTABLE" ]; then
    echo "Google Chrome was not found. Install Google Chrome for your operating system, then rerun this installer." >&2
    return 1
  fi
  BROWSER_EXECUTABLE="$(resolve_browser_path "$BROWSER_EXECUTABLE")"
  say "Using system Google Chrome executable: ${BROWSER_EXECUTABLE}"
}
install_cli_command() {
  if [ -e "$CLI_FILE" ] && [ ! -f "$CLI_FILE" ]; then
    echo "Cannot install ${CLI_FILE}: an existing non-file entry is present." >&2
    return 1
  fi
  if [ -f "$CLI_FILE" ] && ! grep -q "WMADAPTER_INSTALL_MARKER" "$CLI_FILE"; then
    echo "Cannot install ${CLI_FILE}: an unrelated command already exists there." >&2
    return 1
  fi
  mkdir -p "$BIN_DIR"
  cat > "$CLI_FILE" <<WRAPPER
#!/usr/bin/env bash
# WMADAPTER_INSTALL_MARKER
export WMADAPTER_CONFIG="${PROJECT_DIR}/config.yaml"
exec "${PROJECT_DIR}/.venv/bin/wmadapter" "\$@"
WRAPPER
  chmod 755 "$CLI_FILE"
  chown -- "$TARGET_USER:$TARGET_GROUP" "$CLI_FILE"
}
write_service() {
  local login_mode="$1"
  local exec_start="${PROJECT_DIR}/.venv/bin/wmadapter"
  if [ "$USE_XVFB" -eq 1 ]; then
    exec_start="/usr/bin/xvfb-run --auto-servernum --server-args='-screen 0 1440x1000x24' ${exec_start}"
  fi
  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Web Model Adapter — Web-to-API Gateway for AI Agents
After=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=HOME=${TARGET_HOME}
EnvironmentFile=-${PROJECT_DIR}/.env
Environment=WMADAPTER_LOGIN=${login_mode}
Environment=WMADAPTER_XVFB=${USE_XVFB}
Environment=DISPLAY=${DISPLAY_VALUE}
Environment=WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}
Environment=XAUTHORITY=${XAUTHORITY:-}
Environment=XDG_RUNTIME_DIR=/run/user/${TARGET_UID}
Environment=DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-}
ExecStart=${exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
}
prepare_runtime_display() {
  case "$(uname -s)" in
    Linux)
      if ! command -v xvfb-run >/dev/null 2>&1; then
        echo "Xvfb is required for the hidden Chrome runtime on Linux. Install Xvfb, then rerun this installer." >&2
        return 1
      fi
      USE_XVFB=1
      ;;
    *) USE_XVFB=0 ;;
  esac
}
stop_service() {
  systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
}
cleanup_previous_install() {
  stop_service
  # Reinstalling must not destroy browser-managed sessions or local config.
  rm -f -- "$SERVICE_FILE"
  systemctl daemon-reload 2>/dev/null || true
  say "Previous Web Model Adapter service stopped; provider profiles were preserved."
}
cleanup_failed_install() {
  local status=$?
  if [ "$status" -ne 0 ] && [ "$INSTALL_SUCCESS" -ne 1 ]; then
    stop_service 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    echo "Installation failed; the generated service was left at ${SERVICE_FILE} for inspection." >&2
  fi
  return "$status"
}
need systemctl
need curl
trap cleanup_failed_install EXIT

say "Web Model Adapter — Web-to-API Gateway for AI Agents installer"
say "Local installation"

cleanup_previous_install

install_current_os
SERVER_HOST="$API_HOST"
ensure_browser
write_config "$BROWSER_EXECUTABLE" "$SERVER_HOST"
chown -- "$TARGET_USER:$TARGET_GROUP" config.yaml
install_cli_command
prepare_runtime_display
write_service 0
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
health_ready=0
for _ in $(seq 1 30); do
  if curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
    health_ready=1
    break
  fi
  sleep 1
done
if [ "$health_ready" -ne 1 ]; then
  echo "The service was installed but its health endpoint did not become ready." >&2
  systemctl --no-pager --full status "$SERVICE_NAME" >&2 || true
  exit 1
fi
say "Web Model Adapter installed without provider authentication."
say "Provider profiles are preserved across reinstalls."
say "Service started and health check passed. Provider readiness still requires login."
say "Next steps:"
say "  wmadapter provider list"
say "  wmadapter login deepseek"
say "  wmadapter login qwen --google"
say "  wmadapter provider enable qwen"
say "  wmadapter add proxy qwen http://localhost:8080"
say "  wmadapter proxy list"
say "  Service is already running; use wmadapter provider list to inspect providers."
INSTALL_SUCCESS=1
