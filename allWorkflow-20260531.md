# 车型口碑采集系统全流程梳理（2026-05-31）

生成时间：2026-05-31  
服务器：`VM-0-13-ubuntu`  
巡检时间：`2026-05-31T11:07:29+08:00`  
文档目的：记录当前服务器正在运行的车型口碑 Web Demo 的实际运行目录、服务进程、前端设计/制作流程、后端任务编排和完整口碑采集链路。

## 1. 关键结论

- 服务器上当前实际运行的不是 `/opt/codexwork/carFeedback`。
- 当前运行中的 Docker Compose project 只有一个：`koubei-20260527`。
- 当前运行代码目录是：`/opt/codexwork-20260527/vehicle-koubei-web-demo`。
- 当前 Compose 工作目录是：`/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/test`。
- `/opt/codexwork` 作为只读 workspace 挂入容器内 `/workspace`，用于访问采集依赖仓库和 OpenClaw runtime 相关文件。
- OpenClaw gateway 不在 Docker Compose 内运行，而是宿主机 systemd 服务 `openclaw-koubei.service`。
- 本次创建的 `/opt/codexwork/carFeedback` 作为后续文档归档目录，不是当前线上服务执行目录。

## 2. 当前运行目录与配置文件

### 2.1 Docker Compose

| 项 | 值 |
|---|---|
| Compose project | `koubei-20260527` |
| Compose 工作目录 | `/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/test` |
| 主配置文件 | `/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/test/docker-compose.test.yml` |
| 日期覆盖配置 | `/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/test/docker-compose.20260527.yml` |
| 开放访问覆盖配置 | `/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/docker-compose.access-open.override.yml` |
| 镜像构建上下文 | `/opt/codexwork-20260527/vehicle-koubei-web-demo` |

### 2.2 宿主机目录

| 目录 | 用途 | 当前状态 |
|---|---|---|
| `/opt/codexwork-20260527/vehicle-koubei-web-demo` | 当前运行代码与产物目录 | 存在，当前 Compose 从这里构建镜像和挂载产物 |
| `/opt/codexwork-20260527/vehicle-koubei-web-demo/storage/jobs` | job artifact 根目录 | 挂载到容器 `/srv/koubei/jobs` |
| `/opt/codexwork-20260527/vehicle-koubei-web-demo/storage/corpus` | 长期语料库目录 | 挂载到容器 `/srv/koubei/corpus` |
| `/opt/codexwork` | 依赖 workspace | 只读挂载到容器 `/workspace` |
| `/opt/codexwork/vehicle-koubei-web-demo` | 另一个项目 checkout | 存在，但不是当前 Compose 工作目录 |
| `/opt/codexwork/openclaw-koubei-runtime/workspace` | OpenClaw gateway 工作目录 | systemd 服务工作目录 |
| `/home/ubuntu/.openclaw-koubei` | OpenClaw profile/state | 只读挂载到 worker/collector 容器 `/openclaw-state` |
| `/opt/codexwork/carFeedback` | 本文档要求创建的归档目录 | 本次创建，用于保存本文件 |

## 3. 当前服务清单

### 3.1 Docker 容器

| 服务 | 容器 | 状态 | 容器工作目录 | 主要职责 |
|---|---|---|---|---|
| `nginx` | `koubei-20260527-nginx-1` | Up | `/` | 对外暴露 80 端口，代理 Web、API 和 artifact 下载 |
| `web` | `koubei-20260527-web-1` | healthy | `/app` | Next.js 前端 |
| `api` | `koubei-20260527-api-1` | healthy | `/app` | FastAPI 后端，任务创建、状态读取、结果装配 |
| `worker` | `koubei-20260527-worker-2` | healthy | `/app` | 保留 RQ worker 兼容队列能力 |
| `temporal-worker` | `koubei-20260527-temporal-worker-2` | healthy | `/app` | 执行 Temporal workflow 和 activity |
| `autohome-collector` | `koubei-20260527-autohome-collector-1` | healthy | `/app` | 汽车之家采集 service，HTTP `/runs` 接口 |
| `dongchedi-collector` | `koubei-20260527-dongchedi-collector-1` | healthy | `/app` | 懂车帝采集 service，HTTP `/runs` 接口 |
| `temporal` | `koubei-20260527-temporal-1` | healthy | `/etc/temporal` | Temporal server |
| `postgres` | `koubei-20260527-postgres-1` | healthy | `/` | Postgres 数据库 |
| `redis` | `koubei-20260527-redis-1` | healthy | `/data` | Redis 队列/缓存 |

### 3.2 宿主机服务

| 服务 | 状态 | 工作目录 | 端口 | 职责 |
|---|---|---|---|---|
| `openclaw-koubei.service` | active running | `/opt/codexwork/openclaw-koubei-runtime/workspace` | `18790` | OpenClaw gateway，供 worker/collector 提交采集 agent 任务 |

systemd unit 关键配置：

```ini
WorkingDirectory=/opt/codexwork/openclaw-koubei-runtime/workspace
ExecStart=/bin/bash -lc 'OPENCLAW_GATEWAY_TOKEN="$(cat /opt/codexwork/openclaw-koubei-runtime/gateway.token)" exec /usr/bin/openclaw --profile koubei gateway run --port 18790 --auth token --bind lan'
```

### 3.3 端口暴露

| 端口 | 进程 | 对外情况 |
|---|---|---|
| `80` | Docker proxy -> Nginx | 对外开放 Web Demo |
| `18790` | `openclaw-gateway` | 宿主机监听，用于容器访问 OpenClaw gateway |

容器内部端口如 `3000`、`8000`、`8100`、`5432`、`6379`、`7233` 主要在 Compose 网络内使用，未直接对公网暴露。

## 4. 运行配置要点

### 4.1 Web

| 配置 | 当前值 |
|---|---|
| `NEXT_PUBLIC_BASE_PATH` | `/car-user-feedback` |
| `BACKEND_ORIGIN` | `http://api:8000` |

Web 入口：

```text
http://129.211.223.252/car-user-feedback/
```

### 4.2 API

| 配置 | 当前值 |
|---|---|
| `APP_ENV` | `production` |
| `BASE_URL` | `http://129.211.223.252/car-user-feedback` |
| `ARTIFACT_ROOT` | `/srv/koubei/jobs` |
| `CORPUS_ROOT` | `/srv/koubei/corpus` |
| `WORKSPACE_ROOT` | `/workspace` |
| `WORKER_QUEUE_NAME` | `vehicle-koubei-20260527` |
| `TEMPORAL_TASK_QUEUE` | `vehicle-koubei-temporal-20260527` |
| `TASK_CENTER_CREATE_ENABLED` | `true` |

### 4.3 Worker / Temporal Worker

| 配置 | 当前值 |
|---|---|
| `TEMPORAL_ADDRESS` | `temporal:7233` |
| `AUTOHOME_COLLECTOR_SERVICE_URL` | `http://autohome-collector:8100` |
| `DCD_COLLECTOR_SERVICE_URL` | `http://dongchedi-collector:8100` |
| `TEMPORAL_REAL_SINGLE_TASK_PIPELINE_ENABLED` | `true` |
| `ARTIFACT_ROOT` | `/srv/koubei/jobs` |
| `CORPUS_ROOT` / `KOUBEI_CORPUS_ROOT` | `/srv/koubei/corpus` |

注意：`worker` 容器仍显示 `TEMPORAL_TASK_QUEUE=vehicle-koubei-temporal-test`，但主实时 workflow 执行者是 `temporal-worker`，其队列为 `vehicle-koubei-temporal-20260527`。

### 4.4 Collector Service

| 服务 | `COLLECTOR_PLATFORM` | 输入目录/输出目录 |
|---|---|---|
| `autohome-collector` | `autohome` | `/srv/koubei/jobs`、`/srv/koubei/corpus` |
| `dongchedi-collector` | `dongchedi` | `/srv/koubei/jobs`、`/srv/koubei/corpus` |

两个 collector 都通过 `/run/secrets/openclaw_gateway_token` 使用 OpenClaw gateway token，通过 `/openclaw-state` 读取 OpenClaw profile/state。

## 5. 数据库与当前任务概况

数据库容器：

```text
koubei-20260527-postgres-1
POSTGRES_DB=koubei_test
POSTGRES_USER=koubei_test
```

当前统计：

| 表/对象 | 状态 |
|---|---|
| `tasks` | 共 5 个，全部 `completed_degraded` |
| `collection_runs` 汽车之家 | 4 次 `collector_missing_result`，1 次 `rate_limited` |
| `collection_runs` 懂车帝 | 4 次 `succeeded`，1 次 `rate_limited` |
| Temporal running workflow | 当前无 running workflow |

这说明当前服务可运行，但最近采集结果多为降级产出；已知问题主要集中在 OpenClaw API 限流和汽车之家采集任务完成后缺失 artifact。

## 6. 前端制作与设计流程

### 6.1 前端定位

前端目标从早期“五步演示页”调整为“车型口碑工作台”：

- 默认进入工作台，而不是口令页。
- 支持单车型和 2-5 车型对比。
- 让业务用户清楚看到任务队列、采集进度、平台车道、预计完成和结果下载。
- 旧路由仍保留，但从主导航弱化或隐藏。

### 6.2 设计体系

当前设计方向来自 `Flat Beige Workbench UI Redesign`：

- 风格：Flat Design。
- 主背景：米色/浅米色。
- 重点色：墨绿，用于主按钮、活跃导航、正向状态。
- 辅助色：炭黑、金色，用于正文和等待/提示状态。
- 组件策略：高密度工作台，而不是营销落地页。
- 表格优先：任务列表、队列、平台状态、结果矩阵都优先用表格或紧凑数据面板。
- 图标：使用 `lucide-react`。

核心组件：

| 组件 | 用途 |
|---|---|
| `AppShell` / `app-chrome` | 左侧导航和顶部任务状态条 |
| `SidebarNav` | 工作台总览、新建任务、任务中心、结果归档 |
| `TopStatusBar` | 运行任务、可用车道、最近完成等全局状态 |
| `MetricTile` | 关键指标块 |
| `StatusBadge` / `StatusPill` | 状态标签 |
| `DataTable` | 任务列表、结果矩阵 |
| `DensityToggle` | 紧凑模式 |
| `IconButton` / `PrimaryAction` | 图标操作与主操作 |

### 6.3 前端页面结构

| 页面 | 路由 | 作用 |
|---|---|---|
| 工作台总览 | `/` | 运行任务、队列、车道、快速新建入口 |
| 新建任务 | `/tasks/new` | 单车型/多车型输入，候选确认，创建任务 |
| 任务中心 | `/tasks` | 历史任务、筛选、状态、结果入口 |
| 任务详情 | `/tasks/{taskId}` | 任务阶段、平台进度、车道状态、artifact 下载 |
| 结果页 | 任务详情内或兼容旧结果页 | 核心结论、矩阵、词云、Excel/ZIP 下载 |
| 旧流程兼容 | `/passphrase`、`/vehicle`、`/candidates`、`/progress`、`/result` | 保留旧链接，不作为主入口 |

### 6.4 前端业务流程

1. 用户打开 `/car-user-feedback/`。
2. Web 进入工作台总览，读取任务列表、运行状态和最近结果。
3. 用户点击“新建任务”。
4. Web 调用 `POST /api/vehicles/resolve`，解析车型在汽车之家和懂车帝的候选车系。
5. Web 对自动候选或手动输入的 seriesId 调用 `POST /api/vehicles/validate-series`。
6. 用户确认平台候选后，Web 调用 `POST /api/tasks`。
7. 创建成功后跳转任务详情页。
8. 任务详情页轮询 `GET /api/tasks/{task_id}`，展示：
   - 总状态
   - 每个平台状态
   - progress
   - 车道/等待/重试信息
   - artifact 列表
9. 任务完成后，用户在结果仪表盘下载 ZIP、Excel、词云和一页纸报告。

### 6.5 前端制作顺序

推荐维护或继续重做前端时按以下顺序：

1. 先确认 API contract：任务列表、任务详情、artifact list、车型候选、series 校验。
2. 维护全局 shell：左侧导航、顶部状态条、页面内容区。
3. 维护设计 token：颜色、字号、间距、表格密度、状态色。
4. 先保证工作台总览和新建任务可用。
5. 再保证任务中心和任务详情可扫描。
6. 最后完善结果仪表盘、词云、评论样本和下载区。
7. 每次前端改动后验证：
   - `npm --prefix apps/web run typecheck`
   - `npm --prefix apps/web run build`
   - 浏览器检查桌面和平板宽度
   - 检查按钮/表格/状态文案不溢出

## 7. 后端服务进程与职责

### 7.1 总体架构

```text
Browser
  -> Nginx /car-user-feedback/
  -> Next.js web
  -> Nginx /car-user-feedback/api/
  -> FastAPI api
  -> Postgres + Redis
  -> Temporal server
  -> temporal-worker
  -> collector-service(autohome, dongchedi)
  -> OpenClaw gateway
  -> platform collector skills
  -> storage/jobs + storage/corpus
```

### 7.2 API 层职责

FastAPI API 负责：

- 车型候选解析。
- seriesId 校验。
- 创建 `tasks` 和 `task_vehicles`。
- 启动 Temporal workflow。
- 读取任务详情、事件、artifact。
- 提供 artifact 下载。
- 装配结果页所需数据。
- 保持旧 `jobs` 路由兼容。

API 不应该在 `GET` 请求中同步执行长耗时任务，不应在读取结果时临时调用 LLM。

### 7.3 Temporal Worker 职责

Temporal worker 是当前主编排执行者：

- 执行 `SingleVehicleTaskWorkflow`。
- 执行 `ComparisonTaskWorkflow`。
- 调度 collector service。
- 轮询 collection run 状态。
- 处理增量语料和历史语料复用。
- 调用后处理、Hermes/LLM、词云和下载包生成。
- 统一发布 `completed`、`completed_degraded`、`failed` 等终态。

重要原则：

- Worker/Temporal 是唯一总编排层。
- OpenClaw 只是采集执行层。
- Workflow 不直接访问数据库、文件系统或 HTTP collector；这些动作通过 activity 完成。

### 7.4 Collector Service 职责

两个 collector service 暴露一致接口：

| 接口 | 作用 |
|---|---|
| `GET /healthz` | 健康检查 |
| `POST /runs` | 创建采集 run |
| `GET /runs/{run_id}` | 查询 run 状态、进度和失败类别 |
| `POST /runs/{run_id}/cancel` | 请求取消 |

Collector run 输入字段：

- `run_id`
- `task_id`
- `platform`
- `query_key`
- `model_name`
- `series_id`
- `mode`
- `known_links`
- `resume_cursor`
- `max_scan_pages`
- `stop_after_known_pages`

Collector terminal 状态：

- `succeeded`：必须返回实际存在的 `output_path` 或 artifact paths。
- `failed`：必须带 `failure_category`。
- `cancelled`：用户或 worker 取消。

## 8. 完整口碑采集流程

### 8.1 单车型任务流程

```text
用户输入车型
  -> Web 解析候选
  -> 用户确认汽车之家/DCD seriesId
  -> API 创建 task
  -> API 启动 SingleVehicleTaskWorkflow
  -> Worker 创建或复用 collection_runs
  -> autohome-collector 执行汽车之家采集
  -> dongchedi-collector 执行懂车帝采集
  -> Worker 等待两个平台 run terminal
  -> 成功平台导入 corpus
  -> Worker 导出平台 workbook
  -> Worker 执行 postprocess
  -> Worker 执行 Hermes/LLM 摘要
  -> Worker 生成词云和下载包
  -> Worker 写 task_artifacts
  -> Web 展示结果和下载入口
```

### 8.2 增量采集与历史语料

1. Worker 根据 `query_key + platform + series_id` 检查已有语料。
2. 如果存在历史语料，生成 known-links 输入。
3. Collector 执行增量扫描。
4. 如果当前采集失败但历史语料可用，Worker 可以生成 `completed_degraded` 结果。
5. `source_status.json` 用于标记当前平台是实时成功、历史未查新增、还是失败。

### 8.3 双平台并行

- 汽车之家和懂车帝各有独立 collector service。
- 两个平台使用独立 OpenClaw agent/session。
- 前端必须同时展示两条采集线，避免用户误以为串行。
- 任一平台失败时，如果另一平台或历史语料足以生成结果，系统进入 degraded 发布。

### 8.4 对比任务流程

```text
用户输入 2-5 个车型
  -> Web 分别解析候选并确认 seriesId
  -> API 创建 comparison task
  -> ComparisonTaskWorkflow 读取父 task
  -> 为每辆车创建或复用 child single task
  -> 等待每辆车 terminal
  -> 读取 child task 可用产物
  -> 生成 comparison report 和 ZIP
  -> 发布父任务 artifacts
  -> Web 展示多车型对比矩阵和下载入口
```

### 8.5 Artifact 产物

常见产物：

| 类型 | 示例 |
|---|---|
| 原始平台 Excel | `ZJ车型原始口碑.xlsx`、`DCD口碑_车型.xlsx` |
| validation JSON | 采集器输出校验 |
| progress JSON | `collecting_autohome.progress.json`、`collecting_dcd.progress.json` |
| source status | `outputs/raw/{platform}/source_status.json` |
| corpus | 标准化评论语料、known-links、manifest |
| postprocess | 双平台汇总、预处理口碑 |
| Hermes/AI | `final_report.json`、`qa_chunks.json`、`analysis_facts.jsonl` |
| wordcloud | 优点词云、槽点词云、词项清单 |
| downloads | business ZIP、merged raw Excel、one pager Excel |

## 9. OpenClaw 执行层

OpenClaw 在当前系统里不是总编排层，而是 collector service 内部的采集执行层。

### 9.1 运行方式

- 宿主机 systemd 运行 OpenClaw gateway。
- Worker/collector 容器通过 `host.docker.internal:18790` 访问 gateway。
- token 文件以 secret 方式挂入容器。
- OpenClaw profile/state 只读挂载到 `/openclaw-state`。

### 9.2 Agent 与 Skill

当前逻辑上使用：

| 平台 | Agent | Skill |
|---|---|---|
| 汽车之家 | `autohome` / agent pool 中的 `autohome-*` | `auto-koubei-collector` |
| 懂车帝 | `dongchedi` / agent pool 中的 `dongchedi-*` | `dcd-koubei-collector` |

依赖 workspace：

```text
/opt/codexwork/data/repos/vehicle-id-finder
/opt/codexwork/data/repos/auto-koubei-collector
/opt/codexwork/data/repos/dcd-koubei-collector
/opt/codexwork/data/repos/koubei-postprocess
/opt/codexwork/data/repos/koubei-keyword-summary-skill
/opt/codexwork/koubei-wordcloud
```

### 9.3 已知风险

- OpenClaw API rate limit 会导致两个平台采集同时失败。
- 汽车之家曾出现 OpenClaw 任务结束但没有写出 Excel、validation JSON 和 progress JSON 的情况，归类为 `collector_missing_result`。
- OpenClaw runtime workspace 中不应存在会拦截任务的 `BOOTSTRAP.md`。
- OpenClaw agent 执行新 skill 时必须遵守当前 artifact contract，否则 worker 会判定产物缺失。

## 10. 状态与错误分类

### 10.1 Task 状态

| 状态 | 含义 |
|---|---|
| `queued` | 已排队 |
| `running` | 执行中 |
| `retry_wait` | 等待自动重试 |
| `completed` | 完整成功 |
| `completed_degraded` | 降级成功，有可发布结果但部分平台失败 |
| `failed` | 无可发布结果或关键产物缺失 |
| `cancelled` | 已取消 |

### 10.2 Failure Category

| 分类 | 含义 |
|---|---|
| `timeout` | 采集或阶段超时 |
| `network_error` | 网络错误 |
| `rate_limited` | OpenClaw/模型/目标站限流 |
| `collector_missing_result` | collector 结束但 artifact 缺失 |
| `schema_changed` | 目标站结构或输出契约变化 |
| `config_error` | 配置或依赖错误 |
| `agent_busy` | agent 池繁忙 |
| `worker_error` | 未分类 worker 异常 |

## 11. 当前服务器巡检命令

```bash
sudo docker ps
sudo docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.project.working_dir"}}'
sudo docker inspect koubei-20260527-api-1
sudo docker logs --tail=200 koubei-20260527-temporal-worker-2
sudo docker logs --tail=200 koubei-20260527-autohome-collector-1
sudo docker logs --tail=200 koubei-20260527-dongchedi-collector-1
systemctl status openclaw-koubei.service --no-pager -l
curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1:18790/healthz
```

数据库检查：

```bash
sudo docker exec koubei-20260527-postgres-1 \
  psql -U koubei_test -d koubei_test -P pager=off \
  -c "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status;"

sudo docker exec koubei-20260527-postgres-1 \
  psql -U koubei_test -d koubei_test -P pager=off \
  -c "SELECT platform, status, coalesce(failure_category,''), count(*) FROM collection_runs GROUP BY platform,status,failure_category ORDER BY platform,status,failure_category;"
```

Temporal 检查：

```bash
sudo docker exec koubei-20260527-temporal-1 \
  temporal workflow list \
  --address temporal:7233 \
  --namespace default \
  --query 'ExecutionStatus="Running"'
```

## 12. 后续维护建议

1. 明确当前线上执行目录仍是 `/opt/codexwork-20260527/vehicle-koubei-web-demo`，不要误以为 `/opt/codexwork/carFeedback` 已承载运行服务。
2. 如果要把运行目录迁移到 `/opt/codexwork/carFeedback`，需要重新构建 Compose project、迁移 storage/jobs、storage/corpus、Postgres/Redis volumes，并同步 systemd/OpenClaw 路径。
3. 修复汽车之家 `collector_missing_result` 时，优先查看对应 task 的：
   - `logs/collecting_autohome.openclaw.stdout.log`
   - `logs/collecting_autohome.openclaw.stderr.log`
   - `outputs/raw/ZJ...xlsx`
   - `outputs/raw/ZJ...validation.json`
   - `progress/collecting_autohome.progress.json`
4. OpenClaw rate limit 需要从模型配额、gateway 队列、agent 并发和重试策略一起处理。
5. 前端继续迭代时，应优先保持任务中心、任务详情和结果仪表盘可读性，避免回到旧五步演示流程。
6. 新增平台或新 artifact 时，必须先定义 artifact contract，再接入 Temporal activity 和 Web 展示。

