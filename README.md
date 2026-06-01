# 车型口碑情报舱

`carfeedback` 是一个汽车垂媒用户口碑分析服务。用户输入车型后，系统完成车系确认、汽车之家/懂车帝双平台采集、后处理、摘要、词云、AI 一页纸、问答和 ZIP 交付。

本文只保留运行该服务所需的工作流、环境和验证命令。详细交接见 [docs/session-2026-06-01-carfeedback-v3-rebuild.md](docs/session-2026-06-01-carfeedback-v3-rebuild.md)。

## 当前生产状态

- 服务器目录：`/opt/codexwork/carFeedback`
- Compose project：`carfeedback-v3`
- 当前入口：`http://服务器/car-user-feedback`
- 当前端口：`80`
- OpenClaw gateway：宿主机 `127.0.0.1:18790`
- OpenClaw Agent 池：
  - 汽车之家：`autohome-1`、`autohome-2`、`autohome-3`、`autohome-4`
  - 懂车帝：`dongchedi-1`、`dongchedi-2`、`dongchedi-3`、`dongchedi-4`
  - `main` 仅作 fallback

## 必要工作流

```text
Web UI
  -> FastAPI 创建任务
  -> Temporal workflow 编排任务
  -> autohome-collector / dongchedi-collector
  -> OpenClaw 8 Agent 池执行双平台采集
  -> raw Excel + progress JSON + validation JSON
  -> Worker 后处理、摘要、词云、AI 一页纸、QA 语料、ZIP
  -> 任务中心 / 任务详情页交付结果
```

任务创建支持两种采集模式：

- `incremental`：优先复用已有语料，适合日常任务。
- `full_refresh`：强制全量采集，适合验收、排查和基线刷新。

## 运行组件

Docker Compose 内部组件：

- `nginx`：对外入口，转发 Web/API 和 artifact 下载。
- `web`：Next.js 前端，base path 为 `/car-user-feedback`。
- `api`：FastAPI 后端，负责任务创建、车型识别、结果读取和问答接口。
- `temporal`：工作流服务。
- `temporal-worker`：主任务编排执行器。
- `worker`：兼容旧队列和后台处理。
- `autohome-collector`：汽车之家采集入口。
- `dongchedi-collector`：懂车帝采集入口。
- `postgres`：任务、产物和语料索引。
- `redis`：队列和后台任务依赖。

外部运行层：

- `openclaw-koubei.service`：OpenClaw gateway 和 Agent 状态。
- 依赖仓库目录：`/opt/codexwork/data/repos/*` 和 `/opt/codexwork/koubei-wordcloud`。

## 依赖仓库

生产 workspace 需要保持这些目录相对位置：

```text
/opt/codexwork/
  carFeedback/
  data/repos/
    vehicle-id-finder/
    auto-koubei-collector/
    dcd-koubei-collector/
    koubei-postprocess/
    koubei-keyword-summary-skill/
  koubei-wordcloud/
```

当前服务直接依赖：

- `vehicle-id-finder`
- `auto-koubei-collector`
- `dcd-koubei-collector`
- `koubei-postprocess`
- `koubei-keyword-summary-skill`
- `koubei-wordcloud`

## 必要环境

生产环境使用 `.env` 注入配置，不要提交真实密钥。

```env
APP_ENV=production
BASE_URL=http://服务器或域名/car-user-feedback
HTTP_PORT=80
NEXT_PUBLIC_BASE_PATH=/car-user-feedback
BACKEND_ORIGIN=http://api:8000

POSTGRES_DB=koubei
POSTGRES_USER=koubei
POSTGRES_PASSWORD=请替换
DATABASE_URL=postgresql+psycopg://koubei:请替换@postgres:5432/koubei
REDIS_URL=redis://redis:6379/0

ARTIFACT_ROOT=/srv/koubei/jobs
JOB_ARTIFACTS_HOST_PATH=/opt/codexwork/carFeedback/storage/jobs
CORPUS_ROOT=/srv/koubei/corpus
CORPUS_HOST_PATH=/opt/codexwork/carFeedback/storage/corpus
WORKSPACE_ROOT=/workspace

TEMPORAL_ADDRESS=temporal:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=vehicle-koubei-temporal
AUTOHOME_COLLECTOR_SERVICE_URL=http://autohome-collector:8100
DCD_COLLECTOR_SERVICE_URL=http://dongchedi-collector:8100

OPENCLAW_ADAPTER_ENABLED=true
OPENCLAW_ADAPTER_STAGES=collecting_autohome,collecting_dcd
OPENCLAW_GATEWAY_URL=ws://host.docker.internal:18790
OPENCLAW_GATEWAY_TOKEN_FILE=/run/secrets/openclaw_gateway_token
OPENCLAW_GATEWAY_TOKEN_FILE_HOST=/opt/codexwork/carFeedback/.runtime/secrets/openclaw_gateway_token
OPENCLAW_STATE_HOST_PATH=/home/ubuntu/.openclaw-koubei
OPENCLAW_TASK_DB_PATH=/openclaw-state/tasks/runs.sqlite
OPENCLAW_DEVICE_IDENTITY_FILE=/openclaw-state/identity/device.json
OPENCLAW_AGENT_ID=main
OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2,autohome-3,autohome-4
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4
OPENCLAW_ARTIFACT_ROOT_HOST=/opt/codexwork/carFeedback/storage/jobs

TAVILY_API_KEY=请替换
LLM_PROVIDER=deepseek
LLM_API_KEY=请替换
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_BATCH=deepseek-v4-flash
LLM_MODEL_REPORT=deepseek-v4-pro
LLM_MODEL_QA=deepseek-v4-pro

WORDCLOUD_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
```

服务器的 `.runtime` 目录只在服务器维护，不能从本地覆盖。

## 部署与验证

构建并启动：

```bash
cd /opt/codexwork/carFeedback
sudo docker compose -p carfeedback-v3 up -d --build --scale temporal-worker=2
```

检查服务：

```bash
curl -fsS http://127.0.0.1/healthz
curl -fsS http://服务器或域名/car-user-feedback/tasks
curl -fsS 'http://服务器或域名/car-user-feedback/api/tasks?limit=1'
sudo docker compose -p carfeedback-v3 ps
systemctl is-active openclaw-koubei.service
curl -fsS http://127.0.0.1:18790/healthz
```

同步代码到服务器时必须排除运行时和产物目录：

```bash
rsync -az --delete \
  --exclude '.env' \
  --exclude '.runtime' \
  --exclude 'storage' \
  --exclude 'node_modules' \
  --exclude '.next' \
  --exclude '.venv' \
  ./ ubuntu@服务器:/opt/codexwork/carFeedback/
```

## 本地验证

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/python -m pytest apps/api/tests apps/worker/tests apps/collector_service/tests -q
npm --prefix apps/web run typecheck
docker compose config --quiet
```

## 安全边界

- 不提交 `.env`、`.runtime`、OpenClaw token、API key、数据库密码、任务产物或日志。
- 不直接复制服务器 `.runtime` 到本地。
- 不用 OpenClaw task 状态单独判断采集成功，必须同时校验 Excel、progress JSON 和 validation JSON。
- 切换部署前先备份 `.env`，确认 `HTTP_PORT`、`BASE_URL` 和 `NEXT_PUBLIC_BASE_PATH` 一致。
