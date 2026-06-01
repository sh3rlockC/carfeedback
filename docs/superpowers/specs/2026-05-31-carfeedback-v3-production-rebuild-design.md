# carfeedback V3 Production Rebuild Design

Date: 2026-05-31

## Goal

Rebuild the vehicle koubei service as a production replacement in a new server directory:

```text
/opt/codexwork/carFeedback
```

The new service must run in parallel on port `18080` and must not disturb the current port `80` service. It should keep the existing public base path:

```text
/car-user-feedback
```

This rebuild uses the current local codebase at `/Users/xyc/Documents/codexwork/carfeedback` as the development baseline and follows `allWorkflow-20260531-3.md` as the target workflow.

## Scope

The target is a complete production replacement, not only a demo skeleton.

Included:

- Next.js workbench under `/car-user-feedback`.
- FastAPI task-center API.
- Temporal runtime and Temporal worker.
- Autohome and Dongchedi collector services.
- Postgres, Redis, Nginx, artifact storage, corpus storage.
- Real OpenClaw-backed collection.
- Incremental corpus, artifact publishing, reports, QA, comments, time reports, and comparison tasks as defined by the v3 workflow.
- Production configuration rebuilt under `/opt/codexwork/carFeedback`.

Excluded:

- Migrating old task rows, old corpus data, old artifacts, or old reports.
- Replacing the current port `80` service during the parallel validation phase.
- Deleting the legacy `autohome` and `dongchedi` OpenClaw agents before the old service is retired.

## Existing Server Facts

The current service is running as Docker Compose project `koubei-20260527` from the old server workspace. It owns port `80`.

OpenClaw is not part of that Compose project. It is an independent systemd service:

```text
openclaw-koubei.service
```

The gateway listens on port `18790` and uses:

```text
/opt/codexwork/openclaw-koubei-runtime/workspace
/home/ubuntu/.openclaw-koubei
```

Current OpenClaw agents:

- `main`
- `autohome`
- `dongchedi`
- `autohome-1`
- `autohome-2`
- `autohome-3`
- `autohome-4`
- `dongchedi-1`
- `dongchedi-2`
- `dongchedi-3`
- `dongchedi-4`

`main` is the default fallback agent with no platform-specific route. It must not be used for platform collection. The legacy `autohome` and `dongchedi` agents remain available only for the old service during the parallel phase.

## Configuration Layout

The new service owns its runtime files under `/opt/codexwork/carFeedback`:

```text
/opt/codexwork/carFeedback/
  .env
  .runtime/
    secrets/
      openclaw_gateway_token
  storage/
    jobs/
    corpus/
```

The new `.env` is rebuilt from the old production configuration with paths and ports changed for the new deployment.

Important values:

```env
APP_ENV=production
HTTP_PORT=18080
BASE_URL=http://129.211.223.252:18080/car-user-feedback
NEXT_PUBLIC_BASE_PATH=/car-user-feedback
ACCESS_CONTROL_ENABLED=false
NEXT_PUBLIC_ACCESS_CONTROL_ENABLED=false

JOB_ARTIFACTS_HOST_PATH=/opt/codexwork/carFeedback/storage/jobs
CORPUS_HOST_PATH=/opt/codexwork/carFeedback/storage/corpus
OPENCLAW_ARTIFACT_ROOT_HOST=/opt/codexwork/carFeedback/storage/jobs
OPENCLAW_GATEWAY_URL=ws://host.docker.internal:18790
OPENCLAW_GATEWAY_TOKEN_FILE=/run/secrets/openclaw_gateway_token
OPENCLAW_GATEWAY_TOKEN_FILE_HOST=/opt/codexwork/carFeedback/.runtime/secrets/openclaw_gateway_token
OPENCLAW_STATE_HOST_PATH=/home/ubuntu/.openclaw-koubei
OPENCLAW_TASK_DB_PATH=/openclaw-state/tasks/runs.sqlite
OPENCLAW_DEVICE_IDENTITY_FILE=/openclaw-state/identity/device.json
```

The LLM provider, model names, DeepSeek base URL, and API keys are copied from the old production `.env` into the new `.env` without printing secret values in logs or chat.

## 8-Agent Pool Design

The new service must actually dispatch work to the v3 pool:

```env
OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2,autohome-3,autohome-4
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4
OPENCLAW_AGENT_LEASE_SECONDS=2400
OPENCLAW_AGENT_POOL_WAIT_SECONDS=1800
```

The new service must not configure these legacy single-agent values:

```env
OPENCLAW_AUTOHOME_AGENT_ID=autohome
OPENCLAW_DCD_AGENT_ID=dongchedi
```

The legacy agents are not deleted until the old port `80` service is stopped and no longer needs them.

## Agent Dispatch Architecture

Agent selection belongs in the task-center and worker dispatch path, not inside a blind collector process.

Flow:

```text
Temporal Worker
  -> inspect queued collection_runs
  -> inspect running collection_runs.agent_id
  -> choose an available platform agent from the configured pool
  -> mark collection_runs.status=running and collection_runs.agent_id=<real agent id>
  -> send CollectorRunRequest(agent_id=<real agent id>)
  -> Collector Service
  -> OpenClaw Gateway agent call with agentId=<real agent id>
```

Rules:

- A platform run may start only when an agent from that platform pool is available.
- If all platform agents are busy, the run remains `waiting_agent` or `retry_wait`.
- `collection_runs.agent_id` stores the real OpenClaw agent id, for example `autohome-2`.
- The dashboard lane occupancy is derived from real running `collection_runs.agent_id` values.
- Collector services execute the assigned agent; they do not independently pick a different agent.
- `main` is only a fallback for non-platform or accidental unclassified OpenClaw calls and is not part of the visible platform lane pool.

## Required Code Changes

Worker and collector model changes:

- Add optional `agent_id` to `CollectorRunRequest` in both worker and collector service models.
- Add platform agent pool parsing for `OPENCLAW_AUTOHOME_AGENT_IDS` and `OPENCLAW_DCD_AGENT_IDS` in worker runtime code.
- Add a dispatch helper that finds an available agent by subtracting running `collection_runs.agent_id` values from the configured platform pool.
- Update `TaskActivities._dispatch_collection_run` to assign the chosen agent id before submitting to the collector.
- Update `TaskActivities._collector_request` to include the selected agent id.
- Update collector `run_real_collector` to pass `request.agent_id` to the OpenClaw stage runner.
- Update `run_collector_via_openclaw` to accept an assigned agent id while keeping the existing stage default as a fallback.
- Keep API task-load projection aligned with the same pool configuration so displayed total and available lanes match actual dispatch.

Tests:

- Unit test agent pool parsing and available-agent selection.
- Unit test that `CollectorRunRequest.agent_id` is passed from worker dispatch to collector execution.
- Unit test that OpenClaw runner uses the assigned agent id over legacy stage defaults.
- Integration test that two Autohome runs and two Dongchedi runs can be assigned distinct pool agents in parallel without using `autohome` or `dongchedi`.

## Deployment Approach

1. Apply code changes locally.
2. Run local verification:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests apps/collector_service/tests -q
docker compose config --quiet
```

3. Sync the codebase to `/opt/codexwork/carFeedback`.
4. Create new runtime directories and copy only required non-data secrets/config.
5. Build and start the new Compose project with a distinct project name such as `carfeedback-v3`.
6. Verify health:

```bash
curl -fsS http://127.0.0.1:18080/healthz
curl -fsS http://127.0.0.1:18080/car-user-feedback/
```

7. Verify containers, Temporal worker, collector services, and OpenClaw gateway connectivity.
8. Run a small real single-vehicle task and confirm `collection_runs.agent_id` uses `autohome-1..4` and `dongchedi-1..4`.
9. Run a comparison task and confirm child single tasks distribute across the 8-agent pool.

## Cutover Rules

During parallel validation:

- Do not stop the old port `80` service.
- Do not delete the legacy `autohome` and `dongchedi` agents.
- Do not run `docker compose down -v` on the old project.
- Do not remove `/opt/codexwork/openclaw-koubei-runtime` or `/home/ubuntu/.openclaw-koubei`.

After validation:

- Switch the primary domain or port `80` routing to the new service.
- Keep the old service stopped but restorable for one rollback window.
- Delete legacy agents only after confirming no production path references `OPENCLAW_AUTOHOME_AGENT_ID=autohome` or `OPENCLAW_DCD_AGENT_ID=dongchedi`.

## Acceptance Criteria

- `http://129.211.223.252:18080/car-user-feedback/` opens the new workbench.
- Old `http://129.211.223.252/car-user-feedback/` remains available during validation.
- New service has independent Postgres, Redis, artifacts, and corpus.
- New service uses the existing OpenClaw gateway without mutating old service data.
- Real collection runs use only `autohome-1..4` and `dongchedi-1..4`.
- `main`, `autohome`, and `dongchedi` are not used by new platform collection.
- Task-center lane occupancy matches actual assigned agent ids.
- Single task and comparison task workflows complete with downloadable artifacts.
- GET result, artifact, comment, time-report, and QA endpoints remain read-only as required by the v3 workflow.
