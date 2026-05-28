# Server Test Environment And SeriesId Admin Design

## Goal

Build one implementation version that covers:

- A same-server but fully isolated real test environment.
- Long-term `seriesId` preservation from the old system.
- `seriesId` synchronization, import/export, conflict audit, and admin management.
- Real-world validation with true collectors, OpenClaw agents, LLM, and Tavily.
- A reversible migration path from the old system to the new system.

The migration target is new code and configuration plus trusted `seriesId` data. Old job results, downloads, and raw comments are not required for the initial environment bootstrap, but test-environment data generated during validation must be available for final merge into production after audit.

## Environment Isolation

The test environment runs on the same server as old production but is isolated by directory, ports, service stack, volumes, and agents.

- Test code path: `/opt/codexwork-test/vehicle-koubei-web-demo`
- Test entry port: `18080`
- Test deployment id: `koubei-test`
- Production SSH alias: `koubei-prod`, created after user confirms the server candidate.
- Services are independent: Web, API, Postgres, Redis, Temporal, worker, and collector services.
- Data paths are independent: jobs, corpus, artifacts, logs, Postgres volume, and Redis volume.
- OpenClaw agents are independent:
  - Autohome: `autohome-1`, `autohome-2`, `autohome-3`, `autohome-4`
  - Dongchedi: `dongchedi-1`, `dongchedi-2`, `dongchedi-3`, `dongchedi-4`
- Test uses true external systems: collection websites, LLM, Tavily, and OpenClaw.

The `18080` port is initially public and does not require a passphrase. Subdomain access control is deferred until after real testing passes and before production cutover.

Before changing the server, production must be backed up to:

```text
/opt/backups/koubei/YYYYMMDD-HHMMSS/
```

The backup must include the production database, `.env`, compose files, jobs, corpus, and storage. It is compressed as `.tar.gz`.

Resource pressure during testing does not automatically stop or downscale test workers. It must be logged and shown in the deployment terminal. Alert thresholds:

- CPU above 90 percent for 10 minutes.
- Memory above 90 percent.
- Disk above 85 percent.

## SeriesId Data Model

The existing `confirmed_vehicle_series` remains the first-version source of truth for confirmed platform ids. It must be extended into a manageable, auditable asset.

Rules:

- One active record per `query_key + platform`.
- Main statuses: `active`, `archived`, `deleted`.
- Delete is soft delete and can be restored.
- Conflict state is not stored in the main status. Conflicts are stored in a separate conflict table.
- `query` can be edited directly; editing it updates `query_key`.
- If editing `query` creates an active-record conflict, the UI presents a conflict preview and asks the operator which active record to keep.
- `source` remains a free-form string in the first version.
- Syncing old production into test does not preserve old `created_at` or `updated_at`; test import time becomes both.
- Merging test data into production preserves test `created_at` and writes production `updated_at` as the merge import time.

## Sync And Merge

Initial sync:

- Before real testing starts, copy production `confirmed_vehicle_series` into the isolated test database.

During testing:

- The test environment never writes back to the old production database.
- Test-generated `seriesId`, raw comments, and task results remain in the test environment.

Before cutover:

- Sync latest production `confirmed_vehicle_series` into the merge process.
- Perform bidirectional merge audit between production and test.
- Automatically import non-conflicting new records into production.
- Do not automatically overwrite conflicts.
- Store conflicts in a conflict table and export a conflict Excel file for user confirmation.

Audit scope before cutover:

- `seriesId`: new, duplicate, conflict, archived, deleted, restored.
- Raw comments: new, duplicate, conflict.
- Task results: new, duplicate, conflict.

Import behavior:

- Supported formats: Excel and CSV.
- Import always has a preview step.
- Conflicting rows are previewed only and are not written until the user chooses a resolution.
- Rows that exactly duplicate an active `query_key + platform + series_id` are counted in preview only. They are not stored or exported as duplicate files.
- Export format from admin UI: Excel.

## Series Admin UI

Add a hidden admin route:

```text
/series-admin
```

It is not shown in the main navigation and is reachable only by typing the path. During initial testing it is not protected. Before production cutover, subdomain access control must exist, and audit operator identity must switch from manual entry to login identity.

First-version admin features:

- List `seriesId` records.
- Add platform mappings.
- Edit `query`, `seriesId`, URL, title, source, and status.
- Soft delete and restore `seriesId` records.
- Export to Excel.
- Import Excel and CSV.
- Preview imports.
- Preview and resolve conflicts.
- Maintain aliases in a tab on the same page.

Filters:

- Vehicle query search.
- Platform.
- Status.
- Source.
- Updated-at range.
- Conflict status.
- Import batch.

Audit:

- Required for `seriesId` add, edit, soft delete, and restore.
- Audit fields: operator, timestamp, old value, new value, reason.
- In first version, operator is a required text input.
- Reason is required.
- Alias maintenance does not require full audit in first version.

## Alias Management

Alias management is part of `/series-admin`.

First-version rules:

- Use `confirmed_vehicle_series.query` as the canonical model; do not introduce a separate `vehicle_models` table yet.
- Add an alias tab on `/series-admin`.
- Alias supports create, edit, and hard delete.
- Alias does not support restore.
- Alias changes do not require full audit in the first version.
- Business lookup uses aliases. If a user enters an alias, resolve it to canonical query first, then look up confirmed `seriesId`.
- Duplicate aliases are allowed. If one alias points to multiple canonical models, business lookup goes to candidate confirmation rather than auto-selecting one canonical model.

## Real Test Plan

The test environment must validate real external behavior.

Required checks:

- API `healthz` is healthy.
- Port `18080` renders the workbench.
- Production `seriesId` data imports into the test database.
- `/series-admin` can query, filter, edit, import preview, and export.
- Alias lookup affects business lookup.
- One single-vehicle task completes and generates results.
- One or more 2-3 vehicle comparison tasks complete and generate results.
- Four concurrent users complete the pressure test.
- Resource alerts are logged and shown in the terminal.
- Pre-cutover audit report can be generated for `seriesId`, raw comments, and task results.

Pressure test composition:

- 4 concurrent users.
- 1 user runs a single-vehicle query.
- 3 users each run a 2-3 vehicle comparison.
- Vehicles are selected from old production `confirmed_vehicle_series`.
- No hard completion-time threshold.
- Record actual total duration, queue time, agent wait time, failed stages, retries, and bottleneck stages.

## Cutover

Cutover is reversible and must not overwrite old production blindly.

Pre-cutover sequence:

1. Create a full production backup under `/opt/backups/koubei/YYYYMMDD-HHMMSS/`.
2. Sync latest production `confirmed_vehicle_series` into the merge audit.
3. Compare production and test databases.
4. Generate audit reports for `seriesId`, raw comments, and task results.
5. Automatically import non-conflicting additions.
6. Put conflicts in the conflict table and export Excel for user confirmation.
7. Configure subdomain and access control.
8. Switch `/series-admin` operator identity from required text field to access/login identity.
9. Switch production traffic to the new system.
10. Retain old system directory, database snapshot, and backup for 7-14 days.

Post-cutover:

- Test-generated `seriesId`, raw comment corpus, and task results are part of the production data after audit.
- The new system is the only write source.
- Long-term data retention remains enabled.
- If a severe issue appears, rollback uses the production backup.

## Server Operation Policy

Implementation may use local SSH config and establish `koubei-prod`.

If multiple possible servers are found, list candidates and wait for user confirmation. After the user approves the final implementation plan, server work may run automatically according to that plan, including backup, test deployment, service startup, and opening `18080`.
