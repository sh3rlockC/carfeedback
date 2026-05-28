#!/usr/bin/env bash
set -euo pipefail

PROD_ROOT="${PROD_ROOT:-/opt/codexwork/vehicle-koubei-web-demo}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups/koubei}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="${BACKUP_ROOT}/${STAMP}"

mkdir -p "${TARGET}"
cp "${PROD_ROOT}/.env" "${TARGET}/.env"
cp "${PROD_ROOT}/docker-compose.yml" "${TARGET}/docker-compose.yml"
tar -C "${PROD_ROOT}" -czf "${TARGET}/storage.tar.gz" storage || true
docker compose --project-directory "${PROD_ROOT}" exec -T postgres pg_dump -U "${POSTGRES_USER:-koubei}" "${POSTGRES_DB:-koubei}" > "${TARGET}/postgres.sql"
tar -C "${BACKUP_ROOT}" -czf "${TARGET}.tar.gz" "${STAMP}"
echo "${TARGET}.tar.gz"
