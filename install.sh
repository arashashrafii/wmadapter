#!/usr/bin/env bash
set -euo pipefail

API_HOST="127.0.0.1"
API_PORT="11555"
REPO_URL="https://github.com/arashashrafii/webbridgefreeride"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="webbridgefreeride.service"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}"
DISPLAY_VALUE="${DISPLAY:-:0}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

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
  for browser in google-chrome chromium chromium-browser; do
    if command -v "$browser" >/dev/null 2>&1; then
      command -v "$browser"
      return 0
    fi
  done
  return 1
}
resolve_browser_path() {
  local requested="$1"
  case "${requested,,}" in
    google-chrome|googlechrome) requested="google-chrome" ;;
    chromium|chromium-browser) requested="chromium" ;;
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
  local provider="$1" chat_url="$2" headless="$3" executable_path="$4" server_host="$5"
  cat > config.yaml <<YAML
server:
  host: ${server_host}
  port: ${API_PORT}

browser:
  headless: ${headless}
  profile_dir: ./.webbridge-profile
  executable_path: ${executable_path}
  restart_retries: 1

# Provider selected by the installer.
provider_choice: ${provider}

qwen:
  chat_url: ${chat_url}
  auth: google
  profile_dir: ./.webbridge-profile/qwen
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
  file: webbridgefreeride.log
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
  if [ "${INSTALL_BROWSER}" = "yes" ]; then
    .venv/bin/playwright install chromium || true
  fi
}
write_service() {
  local login_mode="$1"
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=WebBridge FreeRide local service
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=-${PROJECT_DIR}/.env
Environment=WEBBRIDGE_LOGIN=${login_mode}
Environment=DISPLAY=${DISPLAY_VALUE}
Environment=XDG_RUNTIME_DIR=${RUNTIME_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python -m webbridgefreeride
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SERVICE
}
start_service() {
  local login_mode="$1"
  write_service "$login_mode"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE_NAME"
}
stop_service() {
  systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
}
wait_health() {
  local i
  for i in $(seq 1 60); do
    if curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}
run_smoke() {
  curl -fsS "http://${API_HOST}:${API_PORT}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${SMOKE_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply exactly: FREERIDE_OK\"}]}" >/tmp/freeride-smoke.json
  grep -q 'FREERIDE_OK' /tmp/freeride-smoke.json
}
install_openclaw_cleanup_plugin() {
  local openclaw_cmd
  openclaw_cmd="$(command -v openclaw || true)"
  if [ -z "$openclaw_cmd" ]; then
    say "OpenClaw not found; cleanup integration can be installed later."
    return 0
  fi
  if "$openclaw_cmd" plugins install --link "${PROJECT_DIR}/openclaw-plugin" --force >/dev/null 2>&1 \
    && "$openclaw_cmd" plugins enable webbridgefreeride-openclaw >/dev/null 2>&1; then
    say "Installed OpenClaw cleanup integration."
  else
    say "Could not install OpenClaw cleanup integration; see README for manual setup."
  fi
}

need curl
need systemctl
API_PORT="$(find_free_port "$API_PORT")"
API_URL="http://${API_HOST}:${API_PORT}/v1"
export API_PORT

say "WebBridge FreeRide installer"
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
SMOKE_MODEL="$PROVIDER-chat"
if [ "$PROVIDER" != "deepseek" ]; then
  say "Using the $PROVIDER browser-backed runtime adapter."
fi

CHAT_URL="$(provider_url "$PROVIDER")"
HEADLESS=$(ask "Run browser headless? (true/false)" "true")
EXECUTABLE_PATH=$(ask "Chrome/Chromium executable path or name (blank for auto-detect)" "")
if [ -z "$EXECUTABLE_PATH" ]; then
  EXECUTABLE_PATH="$(detect_browser || true)"
  if [ -n "$EXECUTABLE_PATH" ]; then
    say "Using installed browser: ${EXECUTABLE_PATH}"
  fi
else
  REQUESTED_BROWSER="$EXECUTABLE_PATH"
  if ! EXECUTABLE_PATH="$(resolve_browser_path "$REQUESTED_BROWSER")"; then
    echo "Browser executable not found: ${REQUESTED_BROWSER}" >&2
    echo "Enter a valid executable path, google-chrome, or chromium." >&2
    exit 1
  fi
fi
SERVER_HOST="$API_HOST"
write_config "$PROVIDER" "$CHAT_URL" "$HEADLESS" "$EXECUTABLE_PATH" "$SERVER_HOST"

say "Manual browser authentication selected; no chatbot credentials will be stored."

INSTALL_BROWSER="no"
INSTALL_BROWSER=$(ask "Install Playwright Chromium if needed? (yes/no)" "yes")
install_current_os
stop_service
say "Opening the browser for manual authentication..."
AUTH_ARGS=(auth "$PROVIDER")
if [ -n "$EXECUTABLE_PATH" ]; then
  AUTH_ARGS+=(--executable-path "$EXECUTABLE_PATH")
fi
if ! .venv/bin/python -m webbridgefreeride "${AUTH_ARGS[@]}"; then
  echo "Manual authentication failed or was cancelled." >&2
  exit 1
fi
start_service 0
install_openclaw_cleanup_plugin

say "Waiting for API health after login..."
if ! wait_health; then
  echo "Server did not become healthy after login. Check webbridgefreeride.install.log." >&2
  exit 1
fi

say "Running complete smoke test..."
if run_smoke; then
  say "WebBridge FreeRide: ${REPO_URL}"
  say "API URL: ${API_URL}"
  say "Health: http://${API_HOST}:${API_PORT}/health"
  say "Models: http://${API_HOST}:${API_PORT}/v1/models"
else
  echo "Smoke test failed. API is up, but provider/chat test did not complete." >&2
  echo "API URL: ${API_URL}" >&2
  exit 1
fi
