#!/usr/bin/env bash
set -euo pipefail

CPU_THRESHOLD="${RESOURCE_WATCH_CPU_THRESHOLD:-90}"
MEMORY_THRESHOLD="${RESOURCE_WATCH_MEMORY_THRESHOLD:-90}"
DISK_THRESHOLD="${RESOURCE_WATCH_DISK_THRESHOLD:-85}"
INTERVAL_SECONDS="${RESOURCE_WATCH_INTERVAL_SECONDS:-60}"
REQUIRED_CPU_HITS="${RESOURCE_WATCH_REQUIRED_CPU_HITS:-10}"
CPU_HITS=0

percent_number() {
  printf '%s' "$1" | tr -d '%' | awk '{ printf "%.0f", $1 }'
}

stats_snapshot() {
  if [[ -n "${RESOURCE_WATCH_SAMPLE_FILE:-}" ]]; then
    cat "${RESOURCE_WATCH_SAMPLE_FILE}"
  else
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}'
  fi
}

disk_snapshot() {
  if [[ -n "${RESOURCE_WATCH_DISK_SAMPLE_FILE:-}" ]]; then
    cat "${RESOURCE_WATCH_DISK_SAMPLE_FILE}"
  else
    df -P /
  fi
}

check_once() {
  local cpu_alert=0
  while IFS=$'\t' read -r name cpu memory; do
    [[ -z "${name}" ]] && continue
    local cpu_value memory_value
    cpu_value="$(percent_number "${cpu}")"
    memory_value="$(percent_number "${memory}")"
    if (( cpu_value >= CPU_THRESHOLD )); then
      cpu_alert=1
    fi
    if (( memory_value >= MEMORY_THRESHOLD )); then
      printf '[resource-alert] memory service=%s usage=%s threshold=%s%%\n' "${name}" "${memory}" "${MEMORY_THRESHOLD}"
    fi
  done < <(stats_snapshot)

  if (( cpu_alert == 1 )); then
    CPU_HITS=$((CPU_HITS + 1))
  else
    CPU_HITS=0
  fi
  if (( CPU_HITS >= REQUIRED_CPU_HITS )); then
    printf '[resource-alert] cpu sustained_hits=%s threshold=%s%% interval_seconds=%s\n' "${CPU_HITS}" "${CPU_THRESHOLD}" "${INTERVAL_SECONDS}"
  fi

  local disk_percent
  disk_percent="$(disk_snapshot | awk 'NR==2 { gsub("%", "", $5); print $5 }')"
  if [[ -n "${disk_percent}" ]] && (( disk_percent >= DISK_THRESHOLD )); then
    printf '[resource-alert] disk usage=%s%% threshold=%s%%\n' "${disk_percent}" "${DISK_THRESHOLD}"
  fi
}

while true; do
  check_once
  if [[ "${RESOURCE_WATCH_ONCE:-0}" == "1" ]]; then
    exit 0
  fi
  sleep "${INTERVAL_SECONDS}"
done
