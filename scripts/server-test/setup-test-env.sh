#!/usr/bin/env bash
set -euo pipefail

TEST_ROOT="${TEST_ROOT:-/opt/codexwork-test/vehicle-koubei-web-demo}"
SOURCE_ROOT="${SOURCE_ROOT:-$(pwd)}"

mkdir -p "${TEST_ROOT}"
source_abs="$(cd "${SOURCE_ROOT}" && pwd -P)"
test_abs="$(cd "${TEST_ROOT}" && pwd -P)"
if [[ "${source_abs}" != "${test_abs}" ]]; then
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'apps/web/node_modules' \
    --exclude 'apps/web/.next' \
    --exclude 'ops/test/.env.test' \
    --exclude 'storage/jobs/*' \
    --exclude 'storage/corpus/*' \
    "${source_abs}/" "${test_abs}/"
fi

mkdir -p "${TEST_ROOT}/storage/jobs" "${TEST_ROOT}/storage/corpus"
if [[ ! -f "${TEST_ROOT}/ops/test/.env.test" ]]; then
  cp "${TEST_ROOT}/ops/test/.env.test.example" "${TEST_ROOT}/ops/test/.env.test"
fi

docker compose \
  --project-name koubei-test \
  --env-file "${TEST_ROOT}/ops/test/.env.test" \
  -f "${TEST_ROOT}/ops/test/docker-compose.test.yml" \
  up -d --build

docker compose \
  --project-name koubei-test \
  --env-file "${TEST_ROOT}/ops/test/.env.test" \
  -f "${TEST_ROOT}/ops/test/docker-compose.test.yml" \
  ps
