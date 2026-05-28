# 车型口碑工作台

车型口碑工作台用于长期沉淀汽车之家、懂车帝双平台原始评论，并为单车型和多车型对比生成可交付的经营分析结果。当前生产版本以 Postgres 作为原始评论可信来源，任务产物和车型目录只作为导出缓存与后续增量采集辅助。

## 功能

- 车型管理：保存已确认的汽车之家、懂车帝 `seriesId`，支持别名、导入、审计和人工修正。
- 长期语料库：评论写入 `koubei_raw_comments`，按平台、车型、来源链接去重，默认不做 3 天自动删除。
- 增量采集：已有历史评论时先导出 known-links，再扫描垂媒最新页，只抓新增评论；无历史时自动全量初始化。
- Agent 并发：OpenClaw 按平台使用 agent pool 和 Redis lease，生产目标为稳定支持 2-4 个用户同时使用。
- 新任务中心：支持单车型任务、多车型对比任务、队列状态、ETA、采集进度和管理操作。
- 智能一页纸：单车型完成前必须生成摘要 Excel、关键词 Excel、词云 PNG、`final_report.json`、`analysis_facts.jsonl`、LLM metrics 和完整 PDF。
- 降级决策：完整报告自动重试失败后进入 `retry_paused`，用户明确选择后才生成降级结果。
- 结果交付：结果页默认展示经营结论和智能一页纸；底层产物折叠展示，并提供一个 ZIP 下载入口。

## 工作流程

```text
用户创建任务
  -> 校验/回填车型 seriesId
  -> 创建或复用 collection_runs
  -> OpenClaw agent pool 领取采集车道
  -> 汽车之家/懂车帝采集器增量抓取
  -> 原始评论 upsert 到 Postgres
  -> 导出车型累计 raw workbook 与 known-links
  -> Hermes/LLM 生成智能报告、词云、PDF、QA 语料
  -> 新任务结果页展示智能一页纸并提供 ZIP
```

多车型对比会为每个车型创建或复用单车型结果，按 `COMPARISON_MODEL_CONCURRENCY` 并行等待子结果；至少两个车型可用时生成对比报告。

## 使用 Agent

生产采集通过 OpenClaw agent 池执行，两个平台分开配置并租约化使用：

```env
OPENCLAW_ADAPTER_ENABLED=true
OPENCLAW_ADAPTER_STAGES=collecting_autohome,collecting_dcd
OPENCLAW_GATEWAY_URL=ws://host.docker.internal:18790
OPENCLAW_GATEWAY_TOKEN_FILE=/run/secrets/openclaw_gateway_token

OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2,autohome-3,autohome-4
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4
OPENCLAW_AGENT_LEASE_SECONDS=2400
OPENCLAW_AGENT_POOL_WAIT_SECONDS=1800
OPENCLAW_TIMEOUT_SECONDS=1800
```

Agent 运维要点：

- `autohome-*` 和 `dongchedi-*` 需要提前在 OpenClaw 中创建，并同步登录态、模型和采集 skill 配置。
- worker 领取 agent lease 后才执行采集；无空闲 agent 时任务进入等待，并持续更新 ETA。
- 采集结束、失败或超时后释放 lease，第三个以上并发任务会排队等待可用车道。
- `known-links.txt` 是增量采集的已知链接清单，采集器用它判断何时停止扫描旧评论。
- `manifest.json` 是车型目录的导出清单，记录车型名、平台 seriesId、原始 Excel 和 known-links 路径。

## 部署环境

核心服务：

- `web`：Next.js 前端。
- `api`：FastAPI API 与任务中心。
- `worker`：RQ worker，负责旧队列任务、采集调度辅助和后台工具。
- `temporal` / `temporal-worker`：新任务中心工作流。
- `postgres`：原始评论、任务、车型和产物元数据。
- `redis`：队列、agent lease 和运行状态。
- `autohome-collector` / `dongchedi-collector`：平台采集服务。
- `nginx`：公网入口和 `/car-user-feedback` base path。

必要环境变量示例：

```env
APP_ENV=production
TASK_CENTER_CREATE_ENABLED=true
ACCESS_CONTROL_ENABLED=false

DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://redis:6379/0
ARTIFACT_ROOT=/srv/koubei/jobs
CORPUS_ROOT=/srv/koubei/corpus
KOUBEI_CORPUS_ROOT=/srv/koubei/corpus
JOB_ARTIFACT_CLEANUP_ENABLED=false

TEMPORAL_ADDRESS=temporal:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=vehicle-koubei-temporal-20260527
WORKER_QUEUE_NAME=vehicle-koubei-20260527
COMPARISON_MODEL_CONCURRENCY=2

LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=...
LLM_MODEL_BATCH=deepseek-v4-flash
LLM_MODEL_REPORT=deepseek-v4-pro
LLM_MODEL_QA=deepseek-v4-pro

HERMES_LLM_MODE=api
HERMES_BATCH_CONCURRENCY=3
HERMES_BATCH_TARGET_BYTES=45000
HERMES_TIMEOUT_SECONDS=180
HERMES_AGGREGATE_TIMEOUT_SECONDS=180
HERMES_JSON_RETRIES=1
```

不要提交 `.env`、OpenClaw token、数据库密码或 LLM key。生产差异配置只提交不含密钥的 compose overlay，例如 `ops/test/docker-compose.20260527.yml`。

## 示例

本地质量门禁：

```bash
uv run --project apps/api --frozen pytest apps/api/tests -q
uv run --project apps/worker --frozen pytest apps/worker/tests -q
npm --prefix apps/web run verify:ui
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
docker compose config --quiet
```

生产栈启动：

```bash
docker compose \
  -p koubei-20260527 \
  --env-file ops/test/.env.20260527 \
  -f ops/test/docker-compose.test.yml \
  -f ops/test/docker-compose.20260527.yml \
  up -d --build --scale worker=2 --scale temporal-worker=2
```

健康检查：

```bash
curl -fsS http://127.0.0.1/healthz
curl -fsSI http://127.0.0.1/car-user-feedback
docker compose -p koubei-20260527 ps
```

历史任务智能一页纸补齐：

```bash
python scripts/task_reports/backfill_task_reports.py --dry-run
python scripts/task_reports/backfill_task_reports.py
```

常用页面：

- `/car-user-feedback`：工作台首页。
- `/car-user-feedback/tasks`：任务中心。
- `/car-user-feedback/tasks/{taskId}`：任务进度和智能一页纸结果。
- `/car-user-feedback/series-admin`：车型 seriesId 管理。

## 目录

```text
apps/web      # Next.js 前端
apps/api      # FastAPI 服务
apps/worker   # Temporal/RQ worker、采集编排、报告生成
ops           # Docker、Nginx、部署 overlay
scripts       # 运维、backfill、车型审计脚本
storage       # 本地数据和产物目录，不提交真实生产数据
```
