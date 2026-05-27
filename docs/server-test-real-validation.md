# Server Test Real Validation

## Environment

- Host alias: `koubei-prod`
- Test root: `/opt/codexwork-test/vehicle-koubei-web-demo`
- Test port: `18080`
- Test compose project: `koubei-test`
- Test agents: `autohome-1..4`, `dongchedi-1..4`

## Required Pass Checks

- `curl -f http://127.0.0.1:18080/healthz` succeeds.
- `curl -fL http://127.0.0.1:18080/car-user-feedback/passphrase` succeeds.
- API health endpoint succeeds through the test stack.
- Production `confirmed_vehicle_series` imports into test.
- `/series-admin` can list, filter, add, edit, soft delete, restore, preview import, commit import, resolve conflicts, and export Excel.
- Alias lookup changes business lookup behavior.
- One single-vehicle task completes with real collectors and real LLM output.
- One comparison task with 2-3 vehicles completes with real collectors and real LLM output.
- Four concurrent users finish the pressure scenario.
- Resource watch logs terminal alerts for CPU, memory, or disk when thresholds are crossed.
- `audit_cutover.py` exports `summary`, `series_diff`, `raw_comment_diff`, and `task_result_diff` sheets.

## Pressure Scenario

- User 1: one single-vehicle query.
- Users 2-4: each runs a 2-3 vehicle comparison.
- Vehicles are selected from imported `confirmed_vehicle_series`.
- Record actual total duration, queue time, agent wait time, failed stages, retries, and bottleneck stages.

## Result Log

| Check | Started At | Finished At | Duration Minutes | Result | Notes |
| --- | --- | --- | ---: | --- | --- |
| Health | | | | | |
| Series sync | | | | | |
| Admin UI | | | | | |
| Alias lookup | | | | | |
| Single task | | | | | |
| Comparison task | | | | | |
| 4-user pressure | | | | | |
| Cutover audit export | | | | | |
