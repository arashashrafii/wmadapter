#!/usr/bin/env bash
set -euo pipefail

API_HOST="127.0.0.1"
API_PORT="8000"
API_URL="http://${API_HOST}:${API_PORT}/v1"
REPO_URL="https://github.com/Shaivpidadi/FreeRide"

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
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
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

# Provider selection is stored for future adapters. Current runtime adapter: deepseek.
provider_choice: ${provider}

deepseek:
  chat_url: ${chat_url}
  timeout_ms: 180000
  login_timeout_ms: 30000
  system_prompt: Absolute mode. Answer briefly. No fluff, no hedging, no follow-up questions unless required.

providers:
  default: deepseek
  enabled:
    - deepseek

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
    kimi) printf '%s' 'https://www.kimi.com/' ;;
    glm) printf '%s' 'https://chatglm.cn/' ;;
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
install_docker() {
  need docker
  docker compose build
}
start_current_os() {
  .venv/bin/python -m webbridgefreeride > webbridgefreeride.install.log 2>&1 &
  echo $! > .webbridgefreeride.pid
}
start_docker() {
  docker compose up -d --build
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
  curl -fsS "http://${API_HOST}:${API_PORT}/ready" >/dev/null || return 1
  curl -fsS "http://${API_HOST}:${API_PORT}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Reply exactly: FREERIDE_OK"}]}' >/tmp/freeride-smoke.json
  grep -q 'FREERIDE_OK' /tmp/freeride-smoke.json
}

say "FreeRide v3 installer"
MODE=$(ask "Install/run with docker or current os? (docker/os)" "os")
case "$MODE" in docker|Docker) MODE="docker" ;; os|OS|current|current-os) MODE="os" ;; *) echo "Invalid mode: $MODE" >&2; exit 1 ;; esac

say "Choose free chatbot provider:"
echo "  1) deepseek (implemented)"
echo "  2) kimi (config only, adapter pending)"
echo "  3) glm (config only, adapter pending)"
echo "  4) qwen (config only, adapter pending)"
CHOICE=$(ask "Provider number" "1")
case "$CHOICE" in
  1|deepseek) PROVIDER="deepseek" ;;
  2|kimi) PROVIDER="kimi" ;;
  3|glm|GLM) PROVIDER="glm" ;;
  4|qwen) PROVIDER="qwen" ;;
  *) echo "Invalid provider: $CHOICE" >&2; exit 1 ;;
esac
if [ "$PROVIDER" != "deepseek" ]; then
  say "Note: $PROVIDER is saved in config, but the current runtime adapter is DeepSeek only."
fi

CHAT_URL=$(ask "Chat authentication/start URL" "$(provider_url "$PROVIDER")")
HEADLESS=$(ask "Run browser headless? (true/false)" "true")
EXECUTABLE_PATH=$(ask "Chrome/Chromium executable path (blank for Playwright default)" "")
if [ -z "$EXECUTABLE_PATH" ]; then EXECUTABLE_PATH=""; fi
SERVER_HOST="$API_HOST"
if [ "$MODE" = "docker" ]; then SERVER_HOST="0.0.0.0"; fi
write_config "$PROVIDER" "$CHAT_URL" "$HEADLESS" "$EXECUTABLE_PATH" "$SERVER_HOST"

AUTH_MODE=$(ask "Login by user/pass or URL/manual authentication? (credentials/url)" "url")
case "$AUTH_MODE" in
  credentials|userpass|user-pass)
    EMAIL=$(ask "Chatbot email/username" "")
    read -r -s -p "Chatbot password: " PASSWORD || true
    printf '\n'
    cat > .env <<ENV
DEEPSEEK_EMAIL=${EMAIL}
DEEPSEEK_PASSWORD=${PASSWORD}
ENV
    chmod 600 .env
    ;;
  url|manual)
    say "Manual/URL authentication selected. Start URL: ${CHAT_URL}"
    say "If the session is not already authenticated, run with headless=false once and log in in the opened browser."
    ;;
  *) echo "Invalid auth mode: $AUTH_MODE" >&2; exit 1 ;;
esac

INSTALL_BROWSER=$(ask "Install Playwright Chromium if needed? (yes/no)" "yes")
if [ "$MODE" = "docker" ]; then
  install_docker
  start_docker
else
  install_current_os
  start_current_os
fi

say "Waiting for API health..."
if ! wait_health; then
  echo "Server did not become healthy. Check webbridgefreeride.install.log or docker compose logs." >&2
  exit 1
fi

say "Running complete smoke test..."
if run_smoke; then
  say "freeride v3 : ${REPO_URL}"
  say "API URL: ${API_URL}"
  say "Health: http://${API_HOST}:${API_PORT}/health"
  say "Models: http://${API_HOST}:${API_PORT}/v1/models"
else
  echo "Smoke test failed. API is up, but provider/chat test did not complete." >&2
  echo "API URL: ${API_URL}" >&2
  exit 1
fi
