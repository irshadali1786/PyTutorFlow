#!/usr/bin/env bash
# Smoke test for the FastAPI side (macOS, also works on Linux).
#   ./scripts/smoke_test.sh
#
# Checks: server reachable, /health works, protected endpoints reject missing/wrong
# API keys and accept the right one. Only READ-ONLY endpoints are called.
# Reads FASTAPI_BASE_URL and API_KEY from the environment or from ./.env (never prints the key).

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

# Read one KEY from .env WITHOUT executing the file.
read_env() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//"
}

BASE_URL="${FASTAPI_BASE_URL:-$(read_env FASTAPI_BASE_URL)}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"
API_KEY="${API_KEY:-$(read_env API_KEY)}"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL  $1"; }

echo "Smoke test against $BASE_URL"

# 0. Local-only sanity check on the URL itself.
case "$BASE_URL" in
  http://127.0.0.1:*|http://localhost:*) ok "base URL is loopback only" ;;
  *) bad "base URL is not 127.0.0.1/localhost - this project must stay local-only" ;;
esac

if [ -z "$API_KEY" ]; then
  bad "API_KEY is empty (set it in .env or export it)"
  echo; echo "Result: $PASS passed, $FAIL failed"; exit 1
fi

# status_of <curl args...>  -> prints HTTP status code, or 000 if the server cannot be reached
status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@" 2>/dev/null || true; }

# 1. Reachable + /health
CODE="$(status_of "$BASE_URL/health")"
if [ "$CODE" = "000" ] || [ -z "$CODE" ]; then
  bad "cannot reach $BASE_URL (is the API running?  python -m app serve)"
  echo; echo "Result: $PASS passed, $FAIL failed"; exit 1
fi
ok "server is reachable"

BODY="$(curl -s --max-time 5 "$BASE_URL/health" 2>/dev/null || true)"
if [ "$CODE" = "200" ] && printf '%s' "$BODY" | grep -Eq '"ok" *: *true'; then
  ok "/health returns 200 with ok=true (no key needed)"
else
  bad "/health returned HTTP $CODE: $BODY"
fi

# 2. API key protection on a read-only endpoint
P="$BASE_URL/bot/offset"
CODE="$(status_of "$P")"
[ "$CODE" = "401" ] && ok "no API key  -> 401" || bad "no API key  -> expected 401, got $CODE"

CODE="$(status_of -H "X-API-Key: definitely-wrong-key" "$P")"
[ "$CODE" = "401" ] && ok "wrong API key -> 401" || bad "wrong API key -> expected 401, got $CODE"

CODE="$(status_of -H "X-API-Key: $API_KEY" "$P")"
[ "$CODE" = "200" ] && ok "correct API key -> 200" || bad "correct API key -> expected 200, got $CODE (does .env match the running server?)"

echo
echo "Result: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
