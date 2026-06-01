# OpenClaw Koubei Skill Flow

## Installed Skills

All six koubei skills are installed in the dedicated `koubei` OpenClaw workspace:

- `vehicle-id-finder`: resolve Autohome and Dongchedi series IDs from a vehicle name.
- `auto-koubei-collector`: collect Autohome koubei and write Excel, validation JSON, and progress JSON.
- `dcd-koubei-collector`: collect Dongchedi koubei and write Excel, validation JSON, failed-pages JSON, and progress JSON.
- `koubei-postprocess`: merge and normalize Autohome and Dongchedi raw outputs.
- `koubei-keyword-summary`: generate dual-platform keyword summary and one-page Excel sheet.
- `koubei-wordcloud`: generate positive/negative wordcloud images and term-list Excel.

OpenClaw workspace path:

```text
/Users/xyc/Documents/codexwork/openclaw-koubei-runtime/workspace/skills
```

Configured local OpenClaw agents:

- `main`: default fallback agent.
- `autohome`: Autohome collection agent.
- `dongchedi`: Dongchedi collection agent.

`autohome` and `dongchedi` keep separate session stores for concurrency, but their `auth-profiles.json`, `auth-state.json`, and `models.json` are synced from the original `main` agent. The runtime workspace `BOOTSTRAP.md` must be absent in production collection mode; otherwise new agent sessions can be intercepted by the first-run bootstrap flow instead of executing the collector skill.

## Runtime Ownership

The Web Demo keeps one pipeline owner:

- API owns passphrase access, vehicle candidate confirmation, job creation, result reads, and AI report endpoints.
- Worker owns queue execution, stage order, database status, progress aggregation, logs, degraded handling, and artifact validation.
- OpenClaw owns agent-executed skill stages only when a stage is listed in `OPENCLAW_ADAPTER_STAGES`.
- The dedicated `koubei` Hermes/OpenClaw profile uses `deepseek/deepseek-v4-flash` as its default execution agent model.
- Collection is routed to platform agents. Batch AI outputs are routed to the `analysis-1,analysis-2` pool with a lightweight lease so concurrent jobs do not pick the same analysis agent.

Current local setting:

```text
OPENCLAW_ADAPTER_ENABLED=true
OPENCLAW_ADAPTER_STAGES=collecting_autohome,collecting_dcd,generating_hermes_outputs,generating_time_report_outputs,generating_comparison_outputs
OPENCLAW_AUTOHOME_AGENT_ID=autohome
OPENCLAW_DCD_AGENT_ID=dongchedi
OPENCLAW_ANALYSIS_AGENT_IDS=analysis-1,analysis-2
OPENCLAW_KEYWORD_SUMMARY_SKILL=sh3rlockC/koubei-keyword-summary-skill
OPENCLAW_WORDCLOUD_SKILL=sh3rlockC/koubei-wordcloud
OPENCLAW_ANALYSIS_PYTHON=/opt/codexwork/koubei-host-venv/bin/python
OPENCLAW_ANALYSIS_ENV_FILE=/opt/codexwork/carFeedback/.runtime/secrets/openclaw-llm.env
OPENCLAW_ANALYSIS_WORKER_ROOT_HOST=/opt/codexwork/carFeedback/apps/worker
OPENCLAW_WORKSPACE_HOST=/opt/codexwork/openclaw-koubei-runtime/workspace
```

This means both collection stages and the three batch AI product stages run through OpenClaw. Postprocess and real-time result-page QA still run in the worker/API process.

## End-To-End Flow

1. User enters a vehicle name in the Web UI.
2. API resolves candidate vehicle IDs.
   Current app route still calls the resolver script from the API container. `vehicle-id-finder` is installed in OpenClaw for the next routing step, but API-to-OpenClaw vehicle resolution is not wired yet.
3. User confirms Autohome and Dongchedi candidates.
4. API creates a job and enqueues worker execution.
5. Worker builds stage commands and routes configured collection and batch AI product stages through OpenClaw.
6. Worker submits `collecting_autohome` to OpenClaw `agent_id=autohome`, which runs `auto-koubei-collector`.
7. Worker submits `collecting_dcd` to OpenClaw `agent_id=dongchedi`, which runs `dcd-koubei-collector`.
8. Worker submits `generating_hermes_outputs`, time reports, and comparison reports to an analysis agent. The OpenClaw contract runs `koubei-keyword-summary-skill`, then `koubei-wordcloud`, then the project DeepSeek analysis command with secrets supplied only through the configured env file.
8. Worker waits for expected artifacts from both collectors.
9. Worker runs `koubei-postprocess` locally to create the dual-platform workbook.
10. Worker runs `koubei-keyword-summary` locally to create the summary Excel and validation JSON.
11. Worker runs `koubei-wordcloud` locally to create wordcloud PNGs and term-list Excel.
12. API assembles result assets and generates the deterministic or LLM-backed one-page AI report.

During collection, the progress API reads `collecting_autohome.progress.json` and `collecting_dcd.progress.json` and exposes each collector's `progress_percent` and `progress_message` to the Web UI. The progress page renders separate 0-100 bars for Autohome and Dongchedi.

## Web Demo LLM Configuration

The local Web Demo API LLM is separate from the Hermes/OpenClaw execution agent model. Batch AI product analysis uses DeepSeek V4 Flash, while the API one-page report and real-time result QA keep DeepSeek V4 Pro through the OpenAI-compatible chat completions API:

```text
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_BATCH=deepseek-v4-flash
LLM_MODEL_REPORT=deepseek-v4-pro
LLM_MODEL_QA=deepseek-v4-pro
```

The API service uses `HTTPReportLLMClient` and calls `/chat/completions` when `LLM_PROVIDER=deepseek`. It also enables OpenAI-style JSON response format for DeepSeek to reduce invalid JSON fallback. If the provider still fails or returns invalid JSON, the one-page report falls back to the deterministic local report.

The result QA path also uses the same DeepSeek-compatible client through `LLM_MODEL_QA`. It first retrieves relevant chunks from the current summary workbook and AI report, then asks the model to generate a Chinese answer from those chunks only. If the QA model is unavailable or returns an empty answer, the API falls back to the deterministic rule-based answer. The API response keeps `citations` empty so the Web UI does not show source evidence.

The Hermes/OpenClaw execution agent profile also uses DeepSeek V4 Flash for skill-running agents:

```text
provider=deepseek
model=deepseek-v4-flash
baseUrl=https://api.deepseek.com
discovery.mdns.mode=off
```

The cloud `koubei` profile disables mDNS/Bonjour discovery because the production worker connects directly to the fixed gateway URL and does not need LAN discovery. Leaving discovery enabled on the cloud host can make the gateway spend startup time in repeated probing and temporarily delay `/healthz`.

## Artifact Contracts

`collecting_autohome` must produce:

- Autohome raw Excel.
- Same-name `.validation.json`.
- `collecting_autohome.progress.json`.

`collecting_dcd` must produce:

- Dongchedi raw Excel.
- Same-name `.validation.json`.
- `collecting_dcd.progress.json`.
- Same-name `.failed-pages.json` when failures exist; an empty array is acceptable when no pages fail.

Worker validates the files after OpenClaw returns or accepts the job. OpenClaw must not change these paths or invent different output names.

## OpenClaw Stage Routing

OpenClaw is a stage adapter, not a second orchestrator.

- Add a stage to `OPENCLAW_ADAPTER_STAGES` only when its output contract is stable.
- Remove a stage from `OPENCLAW_ADAPTER_STAGES` to fall back to the local worker runner.
- Route `collecting_autohome` with `OPENCLAW_AUTOHOME_AGENT_ID`; route `collecting_dcd` with `OPENCLAW_DCD_AGENT_ID`.
- Route batch AI product stages with `OPENCLAW_ANALYSIS_AGENT_IDS`; if the pool is unset, the worker falls back to `OPENCLAW_AGENT_ID`.
- Keep postprocess and real-time result-page QA local.
- Mount OpenClaw state read-only and set `OPENCLAW_TASK_DB_PATH` so the worker can fail fast when an accepted OpenClaw task changes to `failed`, `timed_out`, `cancelled`, or `lost`.
- Do not put `LLM_API_KEY`, gateway token values, or other secrets in OpenClaw task text. The task text may reference `OPENCLAW_ANALYSIS_ENV_FILE` by path only.

## Installed Dependency Notes

Before installing or updating `sh3rlockC/koubei-keyword-summary-skill` or `sh3rlockC/koubei-wordcloud`, run `skill-security-auditor`. Stop if the risk score is above the allowed project threshold. The analysis host also needs `/opt/codexwork/koubei-host-venv` with the runtime packages used by the two skills and the DeepSeek analysis scripts.

- `agent-browser` is available on the host, but the current Autohome production flow primarily uses direct API/detail-page extraction instead of browser-dialog collection.
- Python dependencies verified on host: `requests`, `openpyxl`, `pandas`, `wordcloud`, `jieba`, `matplotlib`, `PIL`, `yaml`.
- Node dependency for `vehicle-id-finder`: `playwright` is installed locally under the OpenClaw `vehicle-id-finder` skill directory.
- Playwright Chromium runtime is installed in the user cache.
- Worker container includes `fonts-noto-cjk`; wordcloud generation passes `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` through `--font-path`.

## Next Upgrade

Wire API vehicle resolution to OpenClaw `vehicle-id-finder` if we want the first step to be agent-managed too. Until then, OpenClaw is active for both collection stages only.
