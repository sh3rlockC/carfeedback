# 车型口碑采集系统融合工作流（2026-05-31 v2）

生成时间：2026-05-31  
来源文档：

- `allWorkflow-20260531.md`
- `docs/superpowers/specs/2026-05-26-flat-beige-workbench-ui-design.md`
- `docs/superpowers/plans/2026-05-26-flat-beige-workbench-ui.md`
- `https://github.com/sh3rlockC/vehicle-koubei-web-demo`（main，`e2106f8`）

本文目标：把当前服务器真实运行链路、Flat Beige Workbench 前端设计目标、2-4 用户并发视角、2-5 车型并行采集与车道排队策略融合成一份修改后的端到端工作流。

## 1. 总体结论

当前线上服务仍运行在：

```text
/opt/codexwork-20260527/vehicle-koubei-web-demo
```

当前 Docker Compose project：

```text
koubei-20260527
```

当前 Compose 工作目录：

```text
/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/test
```

当前新建的归档目录：

```text
/opt/codexwork/carFeedback
```

归档目录只保存文档和后续规划，不承载当前运行服务。

融合后的产品工作流目标是：

1. 用户打开站点后直接进入“车型口碑工作台”。
2. 工作台首屏展示运行中任务、排队状态、车道占用、预计完成时间和快速新建入口。
3. 支持单车型增量采集，也支持 2-5 车型对比任务。
4. 单任务内汽车之家和懂车帝双平台并行采集。
5. 多车型对比任务将每辆车拆成 child single task，并在可用车道中排队并行执行。
6. 结果页以仪表盘方式展示结论、样本量、平台完整度、维度矩阵、词云和下载入口。

## 2. 当前运行服务基线

### 2.1 Compose 与目录

| 项 | 当前值 |
|---|---|
| Compose project | `koubei-20260527` |
| Compose 工作目录 | `/opt/codexwork-20260527/vehicle-koubei-web-demo/ops/test` |
| 主配置 | `docker-compose.test.yml` |
| 日期覆盖 | `docker-compose.20260527.yml` |
| 开放访问覆盖 | `docker-compose.access-open.override.yml` |
| 镜像构建上下文 | `/opt/codexwork-20260527/vehicle-koubei-web-demo` |
| job artifacts | `/opt/codexwork-20260527/vehicle-koubei-web-demo/storage/jobs` |
| corpus | `/opt/codexwork-20260527/vehicle-koubei-web-demo/storage/corpus` |
| 依赖 workspace | `/opt/codexwork` -> 容器内 `/workspace:ro` |
| OpenClaw 工作目录 | `/opt/codexwork/openclaw-koubei-runtime/workspace` |

### 2.2 服务职责

| 服务 | 容器/进程 | 主要职责 |
|---|---|---|
| Nginx | `koubei-20260527-nginx-1` | 暴露 80 端口，代理 Web/API/artifact |
| Web | `koubei-20260527-web-1` | Next.js 工作台前端 |
| API | `koubei-20260527-api-1` | FastAPI，任务创建、状态读取、结果装配 |
| Worker | `koubei-20260527-worker-2` | RQ 兼容能力，非主实时编排 |
| Temporal Worker | `koubei-20260527-temporal-worker-2` | 主 workflow/activity 执行 |
| Temporal | `koubei-20260527-temporal-1` | Workflow runtime |
| Autohome Collector | `koubei-20260527-autohome-collector-1` | 汽车之家采集 `/runs` |
| Dongchedi Collector | `koubei-20260527-dongchedi-collector-1` | 懂车帝采集 `/runs` |
| Postgres | `koubei-20260527-postgres-1` | 任务、车辆、run、artifact、report 持久化 |
| Redis | `koubei-20260527-redis-1` | 队列/缓存兼容 |
| OpenClaw Gateway | `openclaw-koubei.service` | 采集 agent 执行层 |

### 2.3 当前关键配置

| 层 | 配置 | 当前值 |
|---|---|---|
| Web | `NEXT_PUBLIC_BASE_PATH` | `/car-user-feedback` |
| API | `BASE_URL` | `http://129.211.223.252/car-user-feedback` |
| API | `TASK_CENTER_CREATE_ENABLED` | `true` |
| API | `TEMPORAL_TASK_QUEUE` | `vehicle-koubei-temporal-20260527` |
| Worker | `AUTOHOME_COLLECTOR_SERVICE_URL` | `http://autohome-collector:8100` |
| Worker | `DCD_COLLECTOR_SERVICE_URL` | `http://dongchedi-collector:8100` |
| Worker | `TEMPORAL_REAL_SINGLE_TASK_PIPELINE_ENABLED` | `true` |
| Storage | `ARTIFACT_ROOT` | `/srv/koubei/jobs` |
| Storage | `CORPUS_ROOT` | `/srv/koubei/corpus` |

## 3. 融合后的信息架构

主导航：

| 导航项 | 路由 | 职责 |
|---|---|---|
| 工作台总览 | `/` | 查看运行中任务、队列、车道和快速新建 |
| 新建任务 | `/tasks/new` | 创建单车型或 2-5 车型对比任务 |
| 任务中心 | `/tasks` | 筛选任务、查看状态、进入详情和结果 |
| 结果归档 | 后续可接 `/archive` 或复用任务中心 | 统一检索已完成结果 |

兼容路由保留：

```text
/passphrase
/vehicle
/candidates
/progress
/result
```

这些旧路由不作为主入口，但不能破坏已有链接。

## 4. 前端工作台设计工作流

### 4.1 设计目标

前端从工程演示页调整为业务工作台。目标用户是 2-4 个同时使用的内部业务用户，他们需要快速理解：

- 当前有多少任务在运行。
- 哪些任务正在排队。
- 汽车之家/懂车帝车道是否被占用。
- 当前任务预计什么时候完成。
- 哪些任务已降级、失败或可下载。
- 是否可以马上创建单车型或多车型对比任务。

### 4.2 视觉系统

| 设计项 | 规范 |
|---|---|
| 风格 | Flat Design |
| 背景 | 米色、浅米色内容面板 |
| 主色 | 墨绿，用于主按钮和正向状态 |
| 辅助色 | 炭黑、金色，用于正文和提示状态 |
| 密度 | 中高密度，支持紧凑模式 |
| 圆角 | 约 8px |
| 图标 | `lucide-react` |
| 表达方式 | 表格和紧凑数据面板优先 |

### 4.3 工作台总览

总览页首屏优先级：

1. 运行中任务、排队状态、车道占用和预计等待时间。
2. 快速新建查询入口。

首屏模块：

| 模块 | 内容 |
|---|---|
| 顶部状态条 | 运行任务数、可用车道、今日新增评论、最近完成 |
| 主表格 | 运行中任务和队列，展示车型、类型、状态、阶段、预计、创建时间、操作 |
| 快速新建 | 单车型输入、多车型对比入口 |
| 下方辅助区 | 最近完成、异常任务、语料库统计 |

### 4.4 新建任务页

任务模式：

| 模式 | 文案 | 行为 |
|---|---|---|
| 单车型查询 | 一个车型的增量采集、报告和交付物 | 创建 1 个 single task |
| 多车型对比 | 2 到 5 个车型并行采集并生成对比结果 | 创建 comparison task，并拆出 child single tasks |

核心交互：

1. 用户选择单车型或多车型对比。
2. 输入车型名称。
3. 系统先对照历史语料库，再采集新增评论。
4. 多车型任务进入可用车道排队。
5. 按钮文案使用“创建增量采集任务”。

### 4.5 任务中心

任务中心是运行和历史任务的主入口。

筛选项：

| 筛选 | 值 |
|---|---|
| 状态 | 全部、运行、排队、完成、降级、失败 |
| 类型 | 全部、单车型、多车型对比 |
| 关键词 | 任务 ID / 车型 |
| 日期 | 开始日期、结束日期 |
| 平台状态 | 全部、有空闲车道、车道占满 |

表格列：

```text
任务
类型
状态
阶段
预计
标记
创建
完成
操作
```

### 4.6 任务详情页

任务详情首屏顺序：

1. 精确到分钟的预计完成时间。
2. 平台进度和车道状态。
3. 可操作选项。

首屏关键文案：

```text
系统正在根据队列、平台车道和当前阶段估算剩余时间。
```

操作：

| 操作 | 说明 |
|---|---|
| 继续等待 | 刷新任务详情 |
| 生成降级结果 | 后端未开放时保持 disabled |
| 取消任务 | 需要 manage token |
| 失败后重试 | 后续接入 retry/pause-retry 逻辑 |

平台状态区必须同时展示：

- 汽车之家。
- 懂车帝。
- 当前车道状态。
- 当前采集进度。
- 失败、重试、等待或完成状态。

### 4.7 结果仪表盘

结果页优先回答四个问题：

1. 结论是什么。
2. 数据是否完整。
3. 文件在哪里。
4. 是否需要重新采集或接受降级结果。

首屏内容：

| 模块 | 内容 |
|---|---|
| 核心结论 | LLM/Hermes 汇总出的简短判断 |
| 关键指标 | 累计评论、新增评论、平台完整度、报告状态 |
| 维度矩阵 | 单车型为产品机会点，对比任务为车型矩阵 |
| 下载入口 | ZIP、Excel、报告、词云 |

下方内容：

- 一页纸报告。
- 关键词和词云。
- 评论样本。
- 时间报告。
- 采集产物。
- 多车型对比矩阵。

## 5. 后端主工作流

### 5.1 单车型采集

```text
Web 输入车型
  -> POST /api/vehicles/resolve
  -> POST /api/vehicles/validate-series
  -> POST /api/tasks
  -> API 写 tasks/task_vehicles/task_events
  -> API 启动 SingleVehicleTaskWorkflow
  -> Temporal Worker load_task
  -> resolve_vehicle_inputs
  -> create_or_join_collection_run(autohome)
  -> create_or_join_collection_run(dongchedi)
  -> wait_for_collection_runs
  -> import_run_rows_to_corpus
  -> export_vehicle_workbooks
  -> run_postprocess
  -> run_llm_report
  -> publish_full_result 或 publish_degraded_result
  -> Web 读取 GET /api/tasks/{task_id}
```

### 5.2 双平台并行采集

单车型任务内的两个平台并行执行：

| 平台 | Collector Service | OpenClaw Agent | Skill |
|---|---|---|---|
| 汽车之家 | `autohome-collector` | `autohome` 或 `autohome-*` | `auto-koubei-collector` |
| 懂车帝 | `dongchedi-collector` | `dongchedi` 或 `dongchedi-*` | `dcd-koubei-collector` |

并行原则：

- 两个平台都创建或复用独立 `collection_runs`。
- 两个平台都通过 collector service 暴露 `/runs` 状态。
- Worker 轮询两个 run。
- 前端同时展示两条采集线。
- 任一平台失败时，系统尝试使用成功平台和历史语料生成降级结果。

### 5.3 多车型对比并行采集

多车型对比支持 2-5 个车型。

```text
Web 输入 2-5 个车型
  -> 每辆车 resolve + validate
  -> POST /api/tasks task_type=comparison
  -> ComparisonTaskWorkflow load_comparison_task
  -> ensure_vehicle_subworkflow(车型 1)
  -> ensure_vehicle_subworkflow(车型 2)
  -> ...
  -> 每辆车进入 single task 采集链路
  -> wait_for_vehicle_results
  -> generate_comparison_report
  -> publish_comparison_result
  -> Web 展示对比矩阵和 ZIP
```

多车型任务不是在一个 activity 中顺序采集所有车型，而是为每辆车创建或复用 child single task。并行度由可用平台车道、agent 池、Temporal worker 和 collector service 决定。

### 5.4 车道与排队

车道是前端展示给业务用户的调度抽象，对应后端的 collection run、platform agent 和当前运行状态。

| 展示概念 | 后端来源 |
|---|---|
| 运行中任务 | `tasks.status in running/retry_wait` |
| 排队任务 | `tasks.status=queued` 或等待 child task |
| 可用车道 | 平台 agent 配置减去 running collection run |
| 车道占满 | 某平台 running/waiting run 达到可用 agent 数 |
| 预计完成 | `eta_seconds`、`eta_reason` 或按阶段统计推导 |
| 当前瓶颈 | 采集、重试、报告生成、artifact 缺失或限流 |

前端第一版允许从任务状态和平台进度推导车道状态；后续可以补独立聚合接口。

### 5.5 增量采集与历史语料

```text
query_key + platform + series_id
  -> 查 corpus manifest 和 known-links
  -> 生成 known_links / resume_cursor
  -> Collector 执行增量扫描
  -> 新评论导入 corpus
  -> 导出平台 workbook
```

如果实时采集失败：

- 有历史语料：可生成 `completed_degraded`。
- 无可用语料：任务失败。
- 页面必须显示平台完整度和降级原因。

## 6. Collector 与 OpenClaw 执行工作流

### 6.1 Collector Service 接口

| 接口 | 作用 |
|---|---|
| `GET /healthz` | 健康检查 |
| `POST /runs` | 创建/复用采集 run |
| `GET /runs/{run_id}` | 查询 run 状态、进度、失败类别 |
| `POST /runs/{run_id}/cancel` | 请求取消 |

请求字段：

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

### 6.2 OpenClaw 角色

OpenClaw 是可插拔执行层，不是总编排层。

```text
Collector Service
  -> OpenClaw Gateway 18790
  -> platform agent
  -> collector skill
  -> Excel / validation JSON / progress JSON
```

关键要求：

- skill 必须写出 Excel。
- skill 必须写出 validation JSON。
- skill 必须写出 progress JSON。
- artifact 路径必须映射到 `/srv/koubei/jobs`。
- OpenClaw 任务结束但产物缺失时，collector 必须归类为 `collector_missing_result`。

### 6.3 当前风险

| 风险 | 表现 | 处理方向 |
|---|---|---|
| OpenClaw API rate limit | 两个平台同时 `rate_limited` | 降低并发、检查模型额度、增加重试/退避 |
| 汽车之家缺产物 | `OPENCLAW_ARTIFACTS_MISSING` | 检查 agent session、skill 输出路径、validation 和 progress |
| 车道误判 | 前端显示可用但后端 agent 忙 | 增加平台 load 聚合接口 |
| 降级状态不清楚 | 用户不知道是否用历史语料 | 在结果页展示 source_status 和平台完整度 |

## 7. Artifact 与结果发布

### 7.1 Artifact 清单

| 类型 | 路径示例 | 用途 |
|---|---|---|
| 原始 Excel | `outputs/raw/ZJ车型原始口碑.xlsx` | 汽车之家原始口碑 |
| 原始 Excel | `outputs/raw/DCD口碑_车型.xlsx` | 懂车帝原始口碑 |
| validation | `*.validation.json` | 采集契约校验 |
| progress | `progress/collecting_*.progress.json` | 前端进度和 collector 状态 |
| source status | `outputs/raw/{platform}/source_status.json` | 实时/历史/失败标记 |
| corpus | `storage/corpus/.../manifest.json` | 长期语料库 |
| postprocess | `outputs/postprocess/*.xlsx` | 双平台汇总 |
| AI | `outputs/ai/final_report.json` | LLM/Hermes 报告 |
| QA | `outputs/ai/qa_chunks.json` | 问答索引 |
| wordcloud | `outputs/wordcloud/*.png` | 词云 |
| download | `outputs/downloads/*business.zip` | 业务下载包 |

### 7.2 发布规则

| 条件 | 状态 |
|---|---|
| 双平台采集和后处理都成功 | `completed` |
| 部分平台失败，但历史语料或单平台数据可生成结果 | `completed_degraded` |
| 无可用评论或关键 artifact 缺失 | `failed` |
| 用户取消 | `cancelled` |

GET 结果接口只读取已有 artifact 和数据库结果，不应同步生成 LLM 报告。

## 8. 数据库对象工作流

| 表 | 作用 |
|---|---|
| `tasks` | 新任务中心事实源 |
| `task_vehicles` | 单任务车辆、seriesId、平台启用状态 |
| `task_events` | 任务生命周期事件 |
| `collection_runs` | 平台采集 run |
| `collection_run_tasks` | task 与 run 的复用关系 |
| `collector_events` | collector 状态变化 |
| `task_artifacts` | 任务产物列表 |
| `koubei_raw_comments` | 长期评论语料 |
| `job_*` | 旧流程兼容结果 |
| `comparison_*` | 旧对比流程兼容 |

事实源原则：

- 新任务中心优先使用 `tasks/task_vehicles/task_artifacts`。
- 旧 `jobs/comparison_jobs` 保留兼容。
- 对比任务后续应逐步收敛到 native `task_type=comparison`。

## 9. 前端与后端状态映射

| 前端状态 | 后端来源 | 展示建议 |
|---|---|---|
| 等待车道 | `queued`、平台无空闲 run/agent | 金色状态，显示预计等待 |
| 采集中 | collection run `running` | 平台进度条 |
| 检查历史语料 | `checking_incremental` 或已知 links 准备 | 文案强调增量 |
| 报告生成中 | LLM/Hermes activity | 显示报告阶段 |
| 完整成功 | `completed` | 绿色状态 |
| 降级完成 | `completed_degraded` | 黄色/提示状态，展示失败平台 |
| 失败 | `failed` | 红色状态，展示 failure category |
| 结果可下载 | `task_artifacts.downloadable=true` | 下载卡片 |

## 10. 修改后的端到端工作流

### 10.1 单车型用户路径

```text
打开工作台
  -> 看到运行任务/车道/快速新建
  -> 选择单车型查询
  -> 输入车型
  -> resolve 候选
  -> validate seriesId
  -> 创建增量采集任务
  -> 进入任务详情
  -> 同时看到汽车之家和懂车帝采集线
  -> 等待采集/报告/词云完成
  -> 进入结果仪表盘
  -> 下载 ZIP/Excel/词云/一页纸
```

### 10.2 多车型对比用户路径

```text
打开工作台
  -> 选择多车型对比
  -> 输入 2-5 个车型
  -> 每辆车 resolve + validate
  -> 创建对比任务
  -> 每辆车进入 child single task
  -> child tasks 进入可用车道排队
  -> 平台 collector 并行执行
  -> 等待 child task terminal
  -> 生成对比报告和矩阵
  -> 结果仪表盘展示胜者、维度矩阵、下载包
```

### 10.3 管理与异常路径

```text
任务运行中
  -> 用户查看任务详情
  -> 如果车道等待：显示 ETA 和瓶颈
  -> 如果平台失败：显示 failure category
  -> 如果可降级：发布 completed_degraded
  -> 如果用户取消：调用 cancel action
  -> 如果后续重试成功：升级 full result 并重新生成对比
```

## 11. 实施边界与后续补齐

### 11.1 已经具备

- Compose 服务运行。
- Web/API/Temporal/collector/Postgres/Redis/OpenClaw 基础链路。
- 新任务中心相关路由。
- 任务详情和 artifact 展示基础。
- 双平台 collector service。
- OpenClaw agent 执行层。
- 历史语料和降级结果机制。

### 11.2 需要继续强化

| 方向 | 内容 |
|---|---|
| 总览聚合接口 | 直接返回运行数、排队数、平台车道、最近完成 |
| 车道占用准确性 | 从 collection_runs + agent pool 计算 |
| 多用户并发控制 | 单用户限流、全局排队、agent pool |
| 降级操作 | 后端明确开放“生成降级结果”动作 |
| 重试/暂停 | `pause-retry`、失败后手动重试 |
| 平台完整度 | 结果页展示实时成功/历史未查/失败 |
| 运行目录迁移 | 如需改到 `/opt/codexwork/carFeedback`，需迁移 volumes 和 Compose |

### 11.3 前端实施顺序

1. 引入或确认设计 token、`lucide-react`、AppShell、左侧导航、顶部状态条。
2. `/` 默认进入工作台总览。
3. 建立紧凑模式和基础组件。
4. 重做工作台总览。
5. 重做新建任务页，强调“单车型查询 / 多车型对比”。
6. 重做任务中心，突出筛选、可用车道和任务状态。
7. 重做任务详情，首屏展示 ETA、平台进度和操作按钮。
8. 重做结果页，首屏展示核心结论、平台完整度和下载入口。
9. 保留旧五步路由兼容。

### 11.4 验证

前端：

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

后端：

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests -q
```

服务器巡检：

```bash
sudo docker ps
sudo docker logs --tail=200 koubei-20260527-temporal-worker-2
sudo docker logs --tail=200 koubei-20260527-autohome-collector-1
sudo docker logs --tail=200 koubei-20260527-dongchedi-collector-1
systemctl status openclaw-koubei.service --no-pager -l
curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1:18790/healthz
```

数据库：

```bash
sudo docker exec koubei-20260527-postgres-1 \
  psql -U koubei_test -d koubei_test -P pager=off \
  -c "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status;"

sudo docker exec koubei-20260527-postgres-1 \
  psql -U koubei_test -d koubei_test -P pager=off \
  -c "SELECT platform, status, coalesce(failure_category,''), count(*) FROM collection_runs GROUP BY platform,status,failure_category ORDER BY platform,status,failure_category;"
```

## 12. 旧流程删除与新流程采纳决策

本节是对 GitHub 仓库旧 `jobs`/RQ 工作流的取舍结论。任务实施、编排方式、多车型对比、增量语料、GET 结果接口、生命周期和访问控制全部采用当前 v2 新流程；旧流程不再作为目标工作流保留。进度模型暂不在本次调整中修改，后续单独建立新的进度模型。

### 12.1 统一采用新流程的范围

| 范围 | 最终采用 | 删除/废弃的旧行为 |
|---|---|---|
| 任务实施 | `POST /api/tasks` 写 `tasks/task_vehicles/task_events` | `POST /api/jobs` 创建新任务、写 `jobs/job_candidates`、直接入 RQ |
| 编排方式 | Temporal `SingleVehicleTaskWorkflow` / `ComparisonTaskWorkflow` | RQ `worker_jobs.run_job` 作为主执行器 |
| 采集执行 | Collector Service `/runs` + `collection_runs` + OpenClaw stage adapter | worker 直接按本地 stage command 串联采集、后处理和报告 |
| 多车型对比 | `task_type=comparison` 拆 child single tasks，并按车道并行排队 | `comparison_jobs` 逐车同步 `run_job` |
| 增量语料 | `corpus manifest`、`known_links`、`resume_cursor`、`koubei_raw_comments` | 仅基于单次 raw Excel 或近期旧 job 产物复用 |
| GET 结果接口 | 只读 DB 和已发布 artifact | GET 时同步 `ensure_ai_report` / `ensure_qa_chunks` |
| 生命周期 | 新 task artifact 与长期 corpus 分层保留 | 旧 `JOB_ARTIFACT_RETENTION_DAYS` 清理策略支配主结果 |
| 访问控制 | 新工作台统一访问策略和任务创建权限 | 旧 `/api/access/verify` passphrase session 作为主链路门禁 |

### 12.2 新任务实施与编排

新建任务只走 task-center：

```text
Web 输入车型
  -> POST /api/vehicles/resolve
  -> POST /api/vehicles/validate-series
  -> POST /api/tasks
  -> API 写 tasks/task_vehicles/task_events
  -> API 启动 Temporal Workflow
  -> Temporal Worker 创建或复用 collection_runs
  -> Collector Service 执行采集
  -> 导入 corpus
  -> 导出 workbook / report / artifact
  -> 发布 task result
```

执行边界：

- API 不再通过旧 `/api/jobs` 创建新业务任务。
- Worker/RQ 不再作为新任务主编排器。
- OpenClaw 仍是 collector 或 stage 的执行层，不是总编排层。
- 数据库事实源以 `tasks/task_vehicles/task_events/collection_runs/task_artifacts` 为准。
- 旧 `jobs/job_*` 表如仍存在，只能作为历史数据迁移来源，不参与新任务生命周期。

### 12.3 新多车型对比

多车型对比只采用 native comparison task：

```text
Web 输入 2-5 个车型
  -> 每辆车 resolve + validate
  -> POST /api/tasks task_type=comparison
  -> ComparisonTaskWorkflow
  -> 为每辆车创建或复用 child single task
  -> child single tasks 按平台车道并行排队
  -> wait_for_vehicle_results
  -> generate_comparison_report
  -> publish_comparison_result
```

删除旧行为：

- 不再使用 `POST /api/comparisons` 创建新的业务对比任务。
- 不再使用 `comparison_jobs/comparison_vehicles` 作为新对比事实源。
- 不再通过旧 comparison worker 逐车同步调用 `run_job`。
- 不再把近期旧 job 的 `final_report.json` / `analysis_facts.jsonl` 作为新对比任务的主要复用机制。

### 12.4 新增量语料与结果生成

增量采集只采用长期 corpus 机制：

```text
query_key + platform + series_id
  -> 查 corpus manifest
  -> 生成 known_links / resume_cursor
  -> Collector 执行增量扫描
  -> 新评论导入 koubei_raw_comments
  -> 导出平台 workbook
  -> 后处理 / LLM / 词云 / 下载包
```

删除旧行为：

- 不再把单个 job 的 raw Excel 当成长期事实源。
- 不再让旧 `job_artifacts` 决定新任务是否可复用。
- 不再用旧 `analysis_facts.jsonl` 代替 corpus 中的评论事实。
- 失败降级必须基于新任务的实时采集状态和长期 corpus，不再基于旧 job 目录。

### 12.5 新 GET 结果接口

GET 结果接口必须保持只读：

```text
GET /api/tasks/{task_id}
GET /api/tasks/{task_id}/artifacts
GET /api/tasks/{task_id}/result
```

结果读取规则：

- 只读取已经发布的 `task_artifacts`、report JSON、workbook、wordcloud 和 corpus 汇总。
- 不在 GET 请求中触发 LLM 生成。
- 不在 GET 请求中补建 QA chunks。
- 不在 GET 请求中补写 artifact。
- 缺少关键 artifact 时返回明确状态和错误原因，由后台 workflow 或显式 action 处理修复。

删除旧行为：

- 删除旧 `/api/jobs/{job_id}/result` 中 GET 时隐式 `ensure_ai_report` 的设计。
- 删除旧 `/api/jobs/{job_id}/result` 中 GET 时隐式 `ensure_qa_chunks` 的设计。
- 结果页只能展示已完成发布的结果，不能让页面读取动作改变后端状态。

### 12.6 新生命周期与访问控制

生命周期采用新分层：

| 数据 | 保留策略 |
|---|---|
| `koubei_raw_comments` / corpus | 长期保留，作为增量采集和历史降级基础 |
| `task_artifacts` | 按新任务中心策略保留和清理 |
| collector 临时 run 目录 | 可按运行清理策略回收 |
| 旧 `jobs/comparison_jobs` 数据 | 仅迁移或归档，不再作为新任务生命周期的一部分 |

旧清理策略不再支配主流程：

- `JOB_ARTIFACT_RETENTION_DAYS` 不用于决定新任务结果是否过期。
- 旧 cleanup loop 不得删除新 task artifact 或长期 corpus。
- 新结果页的过期、重跑、归档和下载可用性以后由 task-center 生命周期统一定义。

访问控制采用新工作台策略：

- 当前测试环境可继续使用开放访问覆盖。
- 后续生产环境由新工作台统一认证、任务创建权限和管理权限控制。
- 旧 passphrase session 不再作为 `/api/tasks` 主链路的门禁模型。
- 旧 `/api/access/verify` 只可作为历史页面兼容或迁移期入口，不参与新任务创建决策。

### 12.7 进度模型暂不修改

进度相关本次不做删除或重写。现阶段可以继续读取现有任务状态、collector run 状态和已有进度字段来展示进度，但这只是过渡实现。

后续需要单独建立新的进度模型，至少覆盖：

- 单车型任务的阶段权重。
- 双平台 collector run 的实时进度聚合。
- 多车型 child task 的并行进度聚合。
- 队列等待、车道占用和 ETA。
- 降级、重试、取消、过期等 terminal / semi-terminal 状态。

## 13. 最终目标态

目标态不是简单“两个平台能采集”，而是形成一个可被 2-4 个内部用户同时使用的业务工作台：

- 打开即可看到全局负载和可用车道。
- 单车型任务可以双平台并行采集。
- 多车型对比可以把 2-5 个车型拆分到可用车道并行执行。
- 用户能看到预计完成时间和当前瓶颈。
- 采集失败时能明确知道是限流、缺产物、网络、schema 变化还是配置问题。
- 降级结果可读、可下载、可追踪。
- 结果页直接回答业务问题，而不是只展示一串文件。
