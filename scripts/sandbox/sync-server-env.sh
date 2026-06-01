#!/usr/bin/env bash
set -euo pipefail

SERVER_ENV_SOURCE="${SERVER_ENV_SOURCE:-/opt/codexwork/vehicle-koubei-web-demo/.env}"
SANDBOX_ROOT="${SANDBOX_ROOT:-.sandbox/vehicle-koubei}"
SANDBOX_ENV_PATH="${SANDBOX_ENV_PATH:-${SANDBOX_ROOT}/.env}"
SANDBOX_OPENCLAW_TOKEN_SOURCE="${SANDBOX_OPENCLAW_TOKEN_SOURCE:-}"

allowed_keys=" TAVILY_API_KEY LLM_PROVIDER LLM_API_KEY LLM_BASE_URL LLM_MODEL_BATCH LLM_MODEL_REPORT LLM_MODEL_QA "

mkdir -p "$(dirname "$SANDBOX_ENV_PATH")" "$SANDBOX_ROOT/storage/jobs" "$SANDBOX_ROOT/openclaw-state" "$SANDBOX_ROOT/secrets"
tmp_env="$(mktemp)"
source_tmp=""
cleanup() {
  rm -f "$tmp_env"
  if [[ -n "$source_tmp" ]]; then
    rm -f "$source_tmp"
  fi
}
trap cleanup EXIT

{
  printf '%s\n' 'COMPOSE_PROJECT_NAME=vehicle-koubei-sandbox'
  printf '%s\n' 'APP_ENV=sandbox'
  printf '%s\n' 'HTTP_PORT=18080'
  printf '%s\n' 'BASE_URL=http://localhost:18080'
  printf '%s\n' 'NEXT_PUBLIC_BASE_PATH=/car-user-feedback'
  printf '%s\n' 'POSTGRES_DB=koubei'
  printf '%s\n' 'POSTGRES_USER=koubei'
  printf '%s\n' 'POSTGRES_PASSWORD=koubei'
  printf '%s\n' 'DATABASE_URL=postgresql+psycopg://koubei:koubei@postgres:5432/koubei'
  printf '%s\n' 'REDIS_URL=redis://redis:6379/1'
  printf '%s\n' 'WORKER_QUEUE_NAME=vehicle-koubei-sandbox'
  printf '%s\n' 'ARTIFACT_ROOT=/srv/koubei/jobs'
  printf 'JOB_ARTIFACTS_HOST_PATH=%s\n' "$SANDBOX_ROOT/storage/jobs"
  printf 'OPENCLAW_STATE_HOST_PATH=%s\n' "$SANDBOX_ROOT/openclaw-state"
  printf 'OPENCLAW_GATEWAY_TOKEN_FILE_HOST=%s\n' "$SANDBOX_ROOT/secrets/openclaw_gateway_token"
  printf '%s\n' 'JOB_ARTIFACT_RETENTION_DAYS=3'
  printf '%s\n' 'COMPARISON_MODEL_CONCURRENCY=1'
} > "$tmp_env"

if [[ -r "$SERVER_ENV_SOURCE" ]]; then
  readable_source="$SERVER_ENV_SOURCE"
elif [[ "$SERVER_ENV_SOURCE" == *:* ]]; then
  remote_host="${SERVER_ENV_SOURCE%%:*}"
  remote_path="${SERVER_ENV_SOURCE#*:}"
  source_tmp="$(mktemp)"
  ssh "$remote_host" "cat $(printf '%q' "$remote_path")" > "$source_tmp"
  readable_source="$source_tmp"
else
  printf 'server env source is not readable: %s\n' "$SERVER_ENV_SOURCE" >&2
  exit 1
fi

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
  line="${raw_line#export }"
  [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *"="* ]] && continue
  key="${line%%=*}"
  case "$allowed_keys" in
    *" $key "*) printf '%s\n' "$line" >> "$tmp_env" ;;
  esac
done < "$readable_source"

install -m 0600 "$tmp_env" "$SANDBOX_ENV_PATH"

if [[ -n "$SANDBOX_OPENCLAW_TOKEN_SOURCE" && -r "$SANDBOX_OPENCLAW_TOKEN_SOURCE" ]]; then
  install -m 0600 "$SANDBOX_OPENCLAW_TOKEN_SOURCE" "$SANDBOX_ROOT/secrets/openclaw_gateway_token"
elif [[ ! -f "$SANDBOX_ROOT/secrets/openclaw_gateway_token" ]]; then
  : > "$SANDBOX_ROOT/secrets/openclaw_gateway_token"
  chmod 0600 "$SANDBOX_ROOT/secrets/openclaw_gateway_token"
fi

printf 'sandbox env written: %s\n' "$SANDBOX_ENV_PATH"
