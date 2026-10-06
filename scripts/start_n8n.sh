#!/usr/bin/env bash
# Start self-hosted n8n, locally only (macOS, also works on Linux).
#   ./scripts/start_n8n.sh
#
# Reads N8N_ENCRYPTION_KEY etc. from the environment or ./.env (the file is parsed, not executed).
# n8n does NOT read this project's .env by itself - that is why this script exists.

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

read_env() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//"
}
pick() { # pick NAME DEFAULT  -> environment value, else .env value, else DEFAULT
  local cur
  eval "cur=\${$1:-}"
  [ -n "$cur" ] || cur="$(read_env "$1")"
  printf '%s' "${cur:-$2}"
}

if ! command -v n8n >/dev/null 2>&1; then
  echo "n8n is not installed (or not on PATH). See docs/N8N_SETUP.md, step 2." >&2
  exit 1
fi

KEY="$(pick N8N_ENCRYPTION_KEY "")"
if [ -z "$KEY" ]; then
  echo "N8N_ENCRYPTION_KEY is empty. Generate one and put it in .env (docs/N8N_SETUP.md, step 3)." >&2
  exit 1
fi

LISTEN="$(pick N8N_LISTEN_ADDRESS 127.0.0.1)"
if [ "$LISTEN" != "127.0.0.1" ]; then
  echo "Refusing to start: N8N_LISTEN_ADDRESS must be 127.0.0.1 (got '$LISTEN')." >&2
  exit 1
fi

FOLDER="$(pick N8N_USER_FOLDER "$HOME/.n8n-learning-system")"
case "$FOLDER" in
  "$ROOT"|"$ROOT"/*) echo "Refusing to start: N8N_USER_FOLDER must be outside the project folder." >&2; exit 1 ;;
esac
mkdir -p "$FOLDER" && chmod 700 "$FOLDER"

export N8N_ENCRYPTION_KEY="$KEY"
export N8N_LISTEN_ADDRESS="$LISTEN"
export N8N_HOST=127.0.0.1
export N8N_PORT="$(pick N8N_PORT 5678)"
export N8N_PROTOCOL=http
export N8N_USER_FOLDER="$FOLDER"
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_VERSION_NOTIFICATIONS_ENABLED=false
export N8N_PERSONALIZATION_ENABLED=false
# The workflows read the admin alert address with $env.ADMIN_EMAIL, so n8n must be allowed to read env vars.
export N8N_BLOCK_ENV_ACCESS_IN_NODE=false
export ADMIN_EMAIL="$(pick ADMIN_EMAIL "")"
# Deliberately NOT set: WEBHOOK_URL (Gmail is polled by n8n, no webhook, no tunnel).

echo "Starting n8n on http://127.0.0.1:$N8N_PORT  (data folder: $N8N_USER_FOLDER)"
echo "Node $(node --version 2>/dev/null || echo '?'), n8n $(n8n --version 2>/dev/null || echo '?')  - press Ctrl+C to stop."
exec n8n start
