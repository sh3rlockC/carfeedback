# Server Test Series Admin Runbook

## Preconditions

- User has approved the implementation plan.
- `koubei-prod` SSH alias has been confirmed.
- Old production candidate was chosen by the user if multiple SSH hosts matched.
- Test OpenClaw agents exist: `autohome-1..4` and `dongchedi-1..4`.
- The test environment uses `/car-user-feedback` because the shared nginx config redirects `/` there.

## Backup

Run on the production host:

```bash
cd /opt/codexwork/vehicle-koubei-web-demo
PROD_ROOT=/opt/codexwork/vehicle-koubei-web-demo scripts/server-test/backup-production.sh
```

## Deploy Test Environment

Run from the source checkout that should be mirrored into the isolated test root:

```bash
cd /opt/codexwork/vehicle-koubei-web-demo
TEST_ROOT=/opt/codexwork-test/vehicle-koubei-web-demo scripts/server-test/setup-test-env.sh
```

Optional: pre-create and edit the test env before running setup. The setup script preserves this file on later syncs.

```bash
mkdir -p /opt/codexwork-test/vehicle-koubei-web-demo/ops/test
cp ops/test/.env.test.example /opt/codexwork-test/vehicle-koubei-web-demo/ops/test/.env.test
$EDITOR /opt/codexwork-test/vehicle-koubei-web-demo/ops/test/.env.test
```

## Validate

```bash
curl -f http://127.0.0.1:18080/healthz
curl -fL http://127.0.0.1:18080/car-user-feedback/passphrase
docker compose --project-name koubei-test --env-file ops/test/.env.test -f ops/test/docker-compose.test.yml ps
```

## Initial Series Sync

```bash
python scripts/series-admin/sync_confirmed_series.py \
  --source-url "$PROD_DATABASE_URL" \
  --target-url "$TEST_DATABASE_URL" \
  --operator "initial-sync"
```

## Audit Export

```bash
python scripts/series-admin/export_series_audit.py \
  --database-url "$TEST_DATABASE_URL" \
  --output /opt/codexwork-test/audit/series-audit.xlsx
```

## Resource Watch

Run during real testing:

```bash
scripts/server-test/resource-watch.sh
```

Alerts are printed to the terminal only. The script does not stop or downscale services.

## Cutover Audit

Before production cutover:

```bash
python scripts/series-admin/audit_cutover.py \
  --prod-url "$PROD_DATABASE_URL" \
  --test-url "$TEST_DATABASE_URL" \
  --output /opt/codexwork-test/audit/cutover-audit.xlsx
```

Use `docs/server-test-real-validation.md` as the real-environment result log.
