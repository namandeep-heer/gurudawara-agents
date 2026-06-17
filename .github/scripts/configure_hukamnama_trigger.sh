#!/usr/bin/env bash
# Apply hukamnama/trigger.env to .github/workflows/daily-hukamnama.yml

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

TRIGGER_ENV="${TRIGGER_ENV:-hukamnama/trigger.env}"
WORKFLOW_FILE="${WORKFLOW_FILE:-.github/workflows/daily-hukamnama.yml}"

if [[ ! -f "$TRIGGER_ENV" ]]; then
  echo "Missing $TRIGGER_ENV" >&2
  exit 1
fi
if [[ ! -f "$WORKFLOW_FILE" ]]; then
  echo "Missing $WORKFLOW_FILE" >&2
  exit 1
fi

read_env() {
  local key="$1"
  local default="$2"
  local line value
  value="$(grep -E "^${key}=" "$TRIGGER_ENV" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ -z "$value" ]]; then
    echo "$default"
  else
    echo "$value"
  fi
}

ist_to_utc_cron() {
  local ist="$1"
  if [[ ! "$ist" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "Invalid IST time '$ist'. Use HH:MM (24h)." >&2
    exit 1
  fi
  local hour="${BASH_REMATCH[1]}"
  local minute="${BASH_REMATCH[2]}"
  local ist_minutes=$((10#$hour * 60 + 10#$minute))
  local utc_minutes=$((ist_minutes - 330))
  if (( utc_minutes < 0 )); then
    utc_minutes=$((utc_minutes + 1440))
  fi
  local utc_hour=$((utc_minutes / 60))
  local utc_minute=$((utc_minutes % 60))
  printf '%d %d * * *' "$utc_minute" "$utc_hour"
}

format_ist_label() {
  local ist="$1"
  if [[ ! "$ist" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "$ist"
    return
  fi
  local hour="${BASH_REMATCH[1]}"
  local minute="${BASH_REMATCH[2]}"
  local suffix="AM"
  if (( 10#$hour >= 12 )); then suffix="PM"; fi
  local display=$((10#$hour % 12))
  if (( display == 0 )); then display=12; fi
  printf '%d:%s %s' "$display" "$minute" "$suffix"
}

format_utc_label() {
  local cron_expr="$1"
  if [[ "$cron_expr" =~ ^([0-9]+)\ ([0-9]+)\ \*\ \*\ \*$ ]]; then
    printf '%s:%02d' "${BASH_REMATCH[2]}" "${BASH_REMATCH[1]}"
  else
    echo "$cron_expr"
  fi
}

TRIGGER="$(read_env HUKAMNAMA_TRIGGER cron_job_org | tr '[:upper:]' '[:lower:]')"
PRIMARY_IST="$(read_env HUKAMNAMA_PRIMARY_IST 09:00)"
FALLBACK_IST="$(read_env HUKAMNAMA_FALLBACK_IST 09:10)"

if [[ "$TRIGGER" != "cron_job_org" && "$TRIGGER" != "github_schedule" ]]; then
  echo "HUKAMNAMA_TRIGGER must be cron_job_org or github_schedule, got '$TRIGGER'" >&2
  exit 1
fi

PRIMARY_CRON="$(ist_to_utc_cron "$PRIMARY_IST")"
FALLBACK_CRON="$(ist_to_utc_cron "$FALLBACK_IST")"

BEGIN_MARKER="# BEGIN_SCHEDULE"
END_MARKER="# END_SCHEDULE"
BEGIN_LINE="$(grep -n "$BEGIN_MARKER" "$WORKFLOW_FILE" | head -n 1 | cut -d: -f1 || true)"
END_LINE="$(grep -n "$END_MARKER" "$WORKFLOW_FILE" | head -n 1 | cut -d: -f1 || true)"

if [[ -z "$BEGIN_LINE" || -z "$END_LINE" || "$END_LINE" -le "$BEGIN_LINE" ]]; then
  echo "Workflow missing $BEGIN_MARKER / $END_MARKER markers." >&2
  exit 1
fi

TMP="$(mktemp)"
{
  if (( BEGIN_LINE > 1 )); then
    sed -n "1,$((BEGIN_LINE - 1))p" "$WORKFLOW_FILE"
  fi
  sed -n "${BEGIN_LINE}p" "$WORKFLOW_FILE"
  if [[ "$TRIGGER" == "github_schedule" ]]; then
    cat <<EOF
  schedule:
    # $(format_ist_label "$PRIMARY_IST") IST ($(format_utc_label "$PRIMARY_CRON") UTC) — primary
    - cron: "$PRIMARY_CRON"
    # $(format_ist_label "$FALLBACK_IST") IST ($(format_utc_label "$FALLBACK_CRON") UTC) — fallback if the first run missed
    - cron: "$FALLBACK_CRON"
EOF
  fi
  sed -n "${END_LINE}p" "$WORKFLOW_FILE"
  if (( END_LINE < $(wc -l < "$WORKFLOW_FILE") )); then
    sed -n "$((END_LINE + 1)),\$p" "$WORKFLOW_FILE"
  fi
} > "$TMP"
mv "$TMP" "$WORKFLOW_FILE"

REPO_SLUG="namandeep-heer/gurudawara-agents"
if ORIGIN="$(git config --get remote.origin.url 2>/dev/null || true)"; then
  if [[ "$ORIGIN" =~ github\.com[:/](.+)(\.git)?$ ]]; then
    REPO_SLUG="${BASH_REMATCH[1]}"
  fi
fi

echo "Updated $WORKFLOW_FILE"
echo "  Trigger mode: $TRIGGER"
echo "  Primary: $PRIMARY_IST IST -> cron \"$PRIMARY_CRON\""
echo "  Fallback: $FALLBACK_IST IST -> cron \"$FALLBACK_CRON\""
echo

if [[ "$TRIGGER" == "cron_job_org" ]]; then
  cat <<EOF
cron-job.org setup (two daily jobs, timezone Asia/Kolkata):
  Primary:  $PRIMARY_IST
  Fallback: $FALLBACK_IST

  URL:    https://api.github.com/repos/$REPO_SLUG/actions/workflows/daily-hukamnama.yml/dispatches
  Method: POST
  Headers:
    Accept: application/vnd.github+json
    Authorization: Bearer <GITHUB_PAT with Actions: Read and write>
    X-GitHub-Api-Version: 2022-11-28
  Body:   {"ref":"main"}

Store the PAT in cron-job.org only — not in GitHub Secrets.
EOF
else
  cat <<EOF
GitHub schedule mode enabled in workflow.
Disable any cron-job.org jobs for this workflow to avoid duplicate triggers.
Note: public-repo schedule runs may be delayed by up to ~60 minutes.
EOF
fi

echo
echo "Next: git add $WORKFLOW_FILE $TRIGGER_ENV && git commit && git push"