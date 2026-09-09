#!/usr/bin/env bash
set -euo pipefail

API_HOST="127.0.0.1"
API_PORT="11555"
REPO_URL="https://github.com/arashashrafii/wmadapter"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="wmadapter.service"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}"
DISPLAY_VALUE="${DISPLAY:-}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
BROWSER_EXECUTABLE=""
INSTALL_SUCCESS=0
USE_XVFB=0

cd -- "$PROJECT_DIR"

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
  local provider="$1" chat_url="$2" headless="$3" executable_path="$4" server_host="$5" cdp_endpoint="$6"
  cat > config.yaml <<YAML
server:
  host: ${server_host}
  port: ${API_PORT}

browser:
  mode: managed
  headless: ${headless}
  profile_dir: ~/.local/share/wmadapter/profiles/${provider}
  executable_path: ${executable_path}
  cdp_endpoint: ${cdp_endpoint:-null}
  restart_retries: 1

# Provider selected by the installer.
provider_choice: ${provider}

qwen:
  chat_url: ${chat_url}
  auth: google
  profile_dir: ~/.local/share/wmadapter/profiles/qwen
  headless: ${headless}

deepseek:
  transport: web
  chat_url: https://chat.deepseek.com/
  timeout_ms: 180000
  login_timeout_ms: 30000
  system_prompt: Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required.

providers:
  default: ${provider}
  enabled:
    - ${provider}

logging:
  level: INFO
  file: wmadapter.log
  max_bytes: 1000000
  backup_count: 3
YAML
}
provider_url() {
  case "$1" in
    deepseek) printf '%s' 'https://chat.deepseek.com/' ;;
    qwen) printf '%s' 'https://chat.qwen.ai/' ;;
    *) printf '%s' 'https://chat.deepseek.com/' ;;
  esac
}
install_current_os() {
  need python3
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -e .
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
write_service() {
  local login_mode="$1"
  mkdir -p "$SERVICE_DIR"
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
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=-${PROJECT_DIR}/.env
Environment=WMADAPTER_LOGIN=${login_mode}
Environment=WMADAPTER_XVFB=${USE_XVFB}
Environment=DISPLAY=${DISPLAY_VALUE}
Environment=WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}
Environment=XAUTHORITY=${XAUTHORITY:-}
Environment=XDG_RUNTIME_DIR=${RUNTIME_DIR}
Environment=DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-}
ExecStart=${exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
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
start_service() {
  local login_mode="$1"
  write_service "$login_mode"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE_NAME"
}
run_foreground_auth() {
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "interactive_session_unavailable: DISPLAY or WAYLAND_DISPLAY is not set" >&2
    return 1
  fi
  WMADAPTER_LOGIN=1 .venv/bin/wmadapter auth "$PROVIDER" --external-browser
}
stop_service() {
  systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
}
cleanup_previous_install() {
  local profile_root="$HOME/.local/share/wmadapter/profiles"
  stop_service
  pkill -TERM -f "$PROJECT_DIR/.venv/bin/wmadapter auth " 2>/dev/null || true
  local pids
  pids="$(ps -eo pid=,args= | awk -v root="$profile_root" 'index($0,"--user-data-dir=" root "/") > 0 {print $1}')"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
  fi
  rm -rf -- "$profile_root"
  rm -f -- "$SERVICE_FILE" "$PROJECT_DIR/config.yaml"
  systemctl --user daemon-reload 2>/dev/null || true
  say "Previous Web Model Adapter runtime and profile cleaned up."
}
cleanup_failed_install() {
  local status=$?
  if [ "$status" -ne 0 ] && [ "$INSTALL_SUCCESS" -ne 1 ]; then
    stop_service 2>/dev/null || true
    rm -f -- "$SERVICE_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
  fi
  return "$status"
}
wait_service_ready() {
  local i health_response ready_response
  for i in $(seq 1 60); do
    health_response="$(curl -sS "http://${API_HOST}:${API_PORT}/health" 2>/dev/null || true)"
    ready_response="$(curl -sS "http://${API_HOST}:${API_PORT}/ready" 2>/dev/null || true)"
    if printf '%s' "$health_response" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' \
      && printf '%s' "$ready_response" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'; then
      return 0
    fi
    sleep 2
  done
  if [ -n "$health_response" ]; then
    say "API health is not ready: $health_response"
  fi
  if [ -n "$ready_response" ]; then
    say "Provider is not ready: $ready_response"
  fi
  return 1
}
run_smoke() {
  curl -fsS "http://${API_HOST}:${API_PORT}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${SMOKE_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply exactly: WMADAPTER_OK\"}]}" >/tmp/wmadapter-smoke.json
  grep -q 'WMADAPTER_OK' /tmp/wmadapter-smoke.json
}
need curl
need systemctl
trap cleanup_failed_install EXIT
API_PORT="$(find_free_port "$API_PORT")"
API_URL="http://${API_HOST}:${API_PORT}/v1"
export API_PORT

say "Web Model Adapter — Web-to-API Gateway for AI Agents installer"
say "Local installation"

say "Choose free chatbot provider:"
echo "  1) deepseek web (free)"
echo "  2) qwen (implemented)"
CHOICE=$(ask "Provider number" "1")
case "$CHOICE" in
  1|deepseek) PROVIDER="deepseek" ;;
  2|qwen) PROVIDER="qwen" ;;
  *) echo "Invalid provider: $CHOICE" >&2; exit 1 ;;
esac
cleanup_previous_install
SMOKE_MODEL="$PROVIDER-chat"
if [ "$PROVIDER" != "deepseek" ]; then
  say "Using the $PROVIDER browser-backed runtime adapter."
fi

install_current_os
CHAT_URL="$(provider_url "$PROVIDER")"
HEADLESS="false"
SERVER_HOST="$API_HOST"
ensure_browser
write_config "$PROVIDER" "$CHAT_URL" "$HEADLESS" "$BROWSER_EXECUTABLE" "$SERVER_HOST" ""

say "Manual browser authentication selected; no chatbot credentials will be stored."

stop_service
say "Web Model Adapter will open a dedicated system Google Chrome app window for login."
if ! run_foreground_auth; then
  echo "Interactive authentication failed; service was not started." >&2
  exit 1
fi
HEADLESS="true"
write_config "$PROVIDER" "$CHAT_URL" "$HEADLESS" "$BROWSER_EXECUTABLE" "$SERVER_HOST" ""
prepare_runtime_display
start_service 0
say "Waiting for API health and provider readiness after login..."
if ! wait_service_ready; then
  echo "Server did not become healthy after login. Check the legacy wmadapter.install.log." >&2
  exit 1
fi

say "Running complete smoke test..."
if run_smoke; then
say "Web Model Adapter — Web-to-API Gateway for AI Agents: ${REPO_URL}"
  say "API URL: ${API_URL}"
  say "Health: http://${API_HOST}:${API_PORT}/health"
  say "Models: http://${API_HOST}:${API_PORT}/v1/models"
else
  echo "Smoke test failed. API is up, but provider/chat test did not complete." >&2
  echo "API URL: ${API_URL}" >&2
  exit 1
fi
INSTALL_SUCCESS=1
