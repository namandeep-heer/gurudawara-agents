#!/usr/bin/env bash
# Sync secrets from .env to GitHub Actions (one-time / when credentials change).
# Requires: gh auth login

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-.env}"
SECRETS_LIST="${SECRETS_LIST:-.github/secrets.list}"
REPO="${REPO:-}"

if ! command -v gh >/dev/null 2>&1; then
  echo "Install GitHub CLI: https://cli.github.com/" >&2
  exit 1
fi

gh auth status >/dev/null

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.example to .env first." >&2
  exit 1
fi

if [[ ! -f "$SECRETS_LIST" ]]; then
  echo "Missing $SECRETS_LIST" >&2
  exit 1
fi

REPO_ARGS=()
if [[ -n "$REPO" ]]; then
  REPO_ARGS=(--repo "$REPO")
fi

set_secret() {
  local name="$1"
  local value="$2"
  printf '%s' "$value" | gh secret set "$name" "${REPO_ARGS[@]}"
  echo "Set secret: $name"
}

read_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  if [[ -z "$line" ]]; then
    return 1
  fi
  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  if [[ -z "$value" ]]; then
    return 1
  fi
  printf '%s' "$value"
}

SET=0
SKIPPED=0
while IFS= read -r name || [[ -n "$name" ]]; do
  name="${name%%#*}"
  name="$(echo "$name" | xargs)"
  [[ -z "$name" ]] && continue
  if value="$(read_env "$name")"; then
    set_secret "$name" "$value"
    SET=$((SET + 1))
  else
    echo "Skipping $name (empty or missing in $ENV_FILE)" >&2
    SKIPPED=$((SKIPPED + 1))
  fi
done < "$SECRETS_LIST"

echo ""
echo "Done. Set $SET secret(s), skipped $SKIPPED."
echo "Shared config: hukamnama/config.env, scheduled_call/config.env, and service JSON/text files (committed to repo)."