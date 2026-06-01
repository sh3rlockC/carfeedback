# 车型口碑采集系统完整工作流（2026-05-31 v3）

生成时间：2026-05-31  
依据：`allWorkflow-20260531-2.1.md` 的逻辑审计和优化结论  
目标：形成一份只描述新流程的完整工作流文档，用于后续前端、后端、采集、存储和运行治理落地。

## 1. 目标与边界

v3 的目标是把车型口碑采集系统整理成一个可被 2-4 个内部用户同时使用的业务工作台：

1. 用户打开站点后直接进入车型口碑工作台。
2. 工作台首屏展示运行中任务、排队状态、车道占用、预计完成时间和快速新建入口。
3. 支持单车型增量采集。
4. 支持 2-5 车型对比任务。
5. 单车型任务内汽车之家和懂车帝双平台并行采集。
6. 多车型对比任务拆成 child single tasks，并在可用平台车道中排队并行执行。
7. 结果页以仪表盘方式展示结论、样本量、平台完整度、维度矩阵、词云、评论样本、时间报告、QA 和下载入口。

边界：

- 新任务事实源统一为 task-center。
- 新任务编排统一由 Temporal workflow 承担。
- Collector Service 负责平台采集 run。
- OpenClaw 是采集执行层，不是总编排层。
- GET 结果接口只读，不触发生成、补写或修复。
- 进度模型暂不重建，本文件只保留过渡边界。

## 2. 运行服务与职责

| 服务 | 主要职责 |
|---|---|
| Nginx | 对外暴露站点入口，代理 Web、API 和 artifact |
| Web | Next.js 工作台、任务中心、任务详情、结果仪表盘 |
| API | 任务创建、任务查询、action 校验、结果读取、artifact 下载 |
| Temporal | Workflow runtime |
| Temporal Worker | 单车型、对比、报告、发布等 workflow/activity 执行 |
| Autohome Collector | 汽车之家采集 run 管理和执行 |
| Dongchedi Collector | 懂车帝采集 run 管理和执行 |
| Postgres | task、vehicle、run、event、artifact、corpus、report 持久化 |
| Redis | 缓存和运行支持，不作为任务事实源 |
| OpenClaw Gateway | 平台 agent 调度和 collector skill 执行 |

关键逻辑路径：

```text
Web
  -> API
  -> Postgres 写 task-center
  -> Temporal Workflow
  -> Collector Service /runs
  -> OpenClaw platform agent
  -> corpus import
  -> report / artifact publish
  -> Web result dashboard
```

## 3. 信息架构

主导航：

| 导航项 | 路由 | 职责 |
|---|---|---|
| 工作台总览 | `/` | 查看运行任务、排队、车道、异常和快速新建 |
| 新建任务 | `/tasks/new` | 创建单车型或 2-5 车型对比任务 |
| 任务中心 | `/tasks` | 筛选任务、查看状态、进入详情和结果 |
| 任务详情 | `/tasks/{task_id}` | 查看阶段、平台 run、车道、ETA、操作 |
| 结果仪表盘 | `/tasks/{task_id}/result` | 查看报告、矩阵、词云、样本、下载和 QA |
| 时间报告 | `/tasks/{task_id}/time-reports` | 查看和创建时间范围一页纸 |

工作台默认首屏是 `/`，不是营销页或说明页。

## 4. 前端工作台设计

### 4.1 设计目标

目标用户是 2-4 个同时使用的内部业务用户。他们需要快速理解：

- 当前有多少任务在运行。
- 哪些任务正在排队。
- 汽车之家和懂车帝车道是否被占用。
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

首屏优先级：

1. 运行中任务、排队状态、车道占用和预计等待时间。
2. 快速新建查询入口。
3. 异常任务和最近完成任务。

首屏模块：

| 模块 | 内容 |
|---|---|
| 顶部状态条 | 运行任务数、排队任务数、可用车道、今日新增评论、最近完成 |
| 主表格 | 任务、车型、类型、状态、阶段、预计、创建时间、操作 |
| 快速新建 | 单车型输入、多车型对比入口 |
| 异常区 | 限流、缺产物、失败、可降级任务 |
| 语料区 | 语料库总量、今日新增、平台覆盖 |

### 4.4 新建任务页

任务模式：

| 模式 | 行为 |
|---|---|
| 单车型查询 | 创建 1 个 single task，执行增量采集、报告和交付物生成 |
| 多车型对比 | 创建 1 个 comparison task，并拆出 2-5 个 child single tasks |

核心交互：

1. 用户选择任务模式。
2. 输入车型名称。
3. 系统解析并校验双平台 seriesId。
4. 用户确认候选结果。
5. 创建任务。
6. 页面进入任务详情。

### 4.5 任务详情页

详情首屏顺序：

1. 预计完成时间和当前瓶颈。
2. 汽车之家与懂车帝平台状态。
3. child tasks 或平台 runs。
4. 可操作按钮。

操作：

| 操作 | 规则 |
|---|---|
| 刷新状态 | 只读刷新 |
| 取消任务 | 需要 operator 或 admin 权限 |
| 重试失败阶段 | 需要 operator 或 admin 权限，必须满足状态机 guard |
| 发布降级结果 | 需要 admin 权限，并记录降级原因 |

### 4.6 结果仪表盘

结果页优先回答：

1. 结论是什么。
2. 数据是否完整。
3. 哪些平台、车型或时间范围存在降级。
4. 文件在哪里。
5. 是否需要重新采集、重试或接受降级。

首屏内容：

| 模块 | 内容 |
|---|---|
| 核心结论 | LLM/Hermes 汇总出的业务判断 |
| 关键指标 | 累计评论、新增评论、平台完整度、报告状态 |
| 维度矩阵 | 单车型为产品机会点，对比任务为车型矩阵 |
| 下载入口 | ZIP、Excel、报告、词云 |

下方内容：

- 一页纸报告。
- 关键词和词云。
- 评论样本。
- 时间范围报告。
- QA。
- 采集产物。
- 多车型对比矩阵。

## 5. API 工作流

### 5.1 车型解析

```text
POST /api/vehicles/resolve
POST /api/vehicles/validate-series
```

要求：

- 返回汽车之家和懂车帝候选。
- 支持已确认 seriesId 复用。
- 候选必须包含平台、seriesId、标题、URL、来源和置信信息。
- 创建任务前必须完成 seriesId 校验。

### 5.2 任务创建

```text
POST /api/tasks
```

单车型请求核心字段：

- `task_type=single`
- `query`
- `model_name`
- `vehicles[0].platform_inputs`
- `date_range` 可选
- `collection_mode=incremental`

多车型请求核心字段：

- `task_type=comparison`
- `vehicles[2..5]`
- 每辆车独立 `query/model_name/platform_inputs`
- `date_range` 可选
- `collection_mode=incremental`

创建规则：

- API 写 `tasks`。
- API 写 `task_vehicles`。
- API 写 `task_events`。
- API 启动 Temporal workflow。
- API 返回 `task_id`、`status`、`detail_url`。

### 5.3 任务读取

```text
GET /api/tasks
GET /api/tasks/{task_id}
GET /api/tasks/{task_id}/result
GET /api/tasks/{task_id}/artifacts
GET /api/tasks/{task_id}/artifacts/{artifact_id}
```

读取规则：

- GET 接口只读。
- 不触发 LLM。
- 不补建 QA chunks。
- 不补写 artifact。
- 不改变任务状态。

### 5.4 任务操作

```text
POST /api/tasks/{task_id}/actions/cancel
POST /api/tasks/{task_id}/actions/retry
POST /api/tasks/{task_id}/actions/publish-degraded
```

操作规则：

- 每个 action 必须校验当前状态。
- 每个 action 必须写 `task_events`。
- 重复请求必须幂等。
- 取消要向 workflow 和 collector run 传递取消意图。
- 重试不能覆盖已有成功产物，只能创建新 attempt 或新 run。

### 5.5 评论、时间报告和 QA

```text
GET /api/tasks/{task_id}/comments/summary
GET /api/tasks/{task_id}/comments
POST /api/tasks/{task_id}/time-reports
GET /api/tasks/{task_id}/time-reports
GET /api/tasks/{task_id}/time-reports/{report_id}
GET /api/tasks/{task_id}/time-reports/{report_id}/artifacts.zip
POST /api/tasks/{task_id}/qa
```

规则：

- 评论从 `koubei_raw_comments` 读取。
- 时间报告由 workflow 或 activity 生成。
- QA 只基于已发布 report 和 `qa_chunks` 回答。
- QA 不引入外部资料。
- 时间报告和 QA 失败时返回明确错误，不在 GET 中补救。

## 6. 单车型 Workflow

```text
SingleVehicleTaskWorkflow
  -> load_task
  -> resolve_vehicle_inputs
  -> prepare_incremental_context
  -> create_or_join_collection_run(autohome)
  -> create_or_join_collection_run(dongchedi)
  -> wait_for_collection_runs
  -> import_run_rows_to_corpus
  -> export_vehicle_workbooks
  -> run_postprocess
  -> run_report_generation
  -> run_wordcloud_generation
  -> build_qa_chunks
  -> build_business_bundle
  -> validate_artifacts
  -> publish_full_result 或 publish_degraded_result
```

关键规则：

- 汽车之家和懂车帝 run 并行。
- 每个平台 run 独立记录状态、失败类别和产物。
- 采集结果必须先进入 corpus，再由 corpus 导出 workbook。
- 报告、词云、QA chunks、下载包必须在 publish 前完成校验。
- 发布结果必须是原子操作：staging artifact 校验通过后再写 `task_artifacts`。

## 7. 多车型对比 Workflow

```text
ComparisonTaskWorkflow
  -> load_comparison_task
  -> validate_vehicle_count(2..5)
  -> ensure_child_single_task(vehicle 1)
  -> ensure_child_single_task(vehicle 2)
  -> ...
  -> wait_for_child_results
  -> collect_vehicle_summaries
  -> generate_comparison_matrix
  -> generate_comparison_report
  -> build_comparison_bundle
  -> validate_artifacts
  -> publish_comparison_result
```

并行原则：

- 每辆车都是一个 child single task。
- child single tasks 按平台车道、agent 池和全局并发限制排队。
- 对比任务不直接执行平台采集。
- 对比任务只等待 child task 的 terminal 状态。

失败和降级规则：

- 至少 2 个车型有可用结果，才允许发布对比结果。
- 部分车型失败时，对比任务可发布 `completed_degraded`。
- 被排除车型必须记录原因。
- 所有车型失败或可用车型少于 2 个时，对比任务失败。

## 8. Collector 与 OpenClaw

### 8.1 Collector Service

接口：

| 接口 | 作用 |
|---|---|
| `GET /healthz` | 健康检查 |
| `POST /runs` | 创建或复用采集 run |
| `GET /runs/{run_id}` | 查询 run 状态、进度、失败类别 |
| `POST /runs/{run_id}/cancel` | 请求取消 |

run 输入字段：

- `run_id`
- `task_id`
- `task_vehicle_id`
- `platform`
- `query_key`
- `model_name`
- `series_id`
- `mode`
- `known_links`
- `resume_cursor`
- `max_scan_pages`
- `stop_after_known_pages`
- `idempotency_key`

run 状态：

```text
queued
running
rate_limited
retry_wait
succeeded
failed
cancel_requested
cancelled
timed_out
```

### 8.2 OpenClaw 角色

```text
Collector Service
  -> OpenClaw Gateway
  -> platform agent
  -> collector skill
  -> raw data / validation / progress
```

要求：

- OpenClaw 不创建 task。
- OpenClaw 不写 task 状态。
- OpenClaw 只负责平台采集执行。
- Collector Service 负责把 OpenClaw 结果映射为 run 状态。
- 产物缺失必须归类为 `collector_missing_result`。

### 8.3 车道与并发

车道是业务展示和调度抽象：

| 概念 | 来源 |
|---|---|
| 平台车道数 | 平台 agent 配置 |
| 运行中车道 | `collection_runs.status in running/retry_wait` |
| 可用车道 | 平台车道数减去运行中车道 |
| 排队任务 | 等待 collection run 或 child task 的任务 |
| 当前瓶颈 | 采集、限流、重试、报告生成、artifact 校验 |

并发约束：

- 全局最大运行任务数。
- 单用户最大运行任务数。
- 单平台最大运行 run 数。
- 单 comparison task 最大 child 并行数。
- 平台限流时进入退避，不抢占正常运行 run。

## 9. 增量语料

### 9.1 语料键

语料以以下字段定位：

```text
query_key
platform
series_id
comment_id 或 source_url_hash
published_at
```

核心对象：

- `koubei_raw_comments`
- `corpus_manifests`
- `known_links`
- `resume_cursor`
- `source_status`

### 9.2 增量流程

```text
prepare_incremental_context
  -> 查 corpus manifest
  -> 读取 known_links
  -> 读取 resume_cursor
  -> collector run 增量扫描
  -> 新评论去重
  -> 写 koubei_raw_comments
  -> 更新 corpus manifest
  -> 导出本次任务 workbook
```

### 9.3 降级规则

平台失败时，系统按以下顺序判断：

1. 是否有另一个平台实时成功。
2. 是否有该平台历史语料。
3. 历史语料是否满足最小样本量。
4. 历史语料是否在允许时间范围内。
5. 报告是否能明确标记平台完整度和 source_status。

状态规则：

| 条件 | 任务状态 |
|---|---|
| 双平台实时成功，artifact 校验通过 | `completed` |
| 单平台实时成功 + 另一平台可用历史语料 | `completed_degraded` |
| 双平台都失败，但历史语料满足报告阈值 | `completed_degraded` |
| 无可用评论或关键 artifact 缺失 | `failed` |
| 用户取消 | `cancelled` |

## 10. 报告、评论、时间报告与 QA

### 10.1 主报告

主报告在 workflow 中生成：

```text
corpus subset
  -> analysis_facts
  -> LLM/Hermes batch analysis
  -> final_report.json
  -> summary workbook
  -> wordcloud
  -> qa_chunks
```

要求：

- LLM 输入只使用分析必要字段。
- 用户名、来源链接、购车地、精确地点等非必要字段不进入 prompt。
- LLM 失败时允许规则 fallback，但必须记录 `degraded_reason`。

### 10.2 评论预览

评论预览从 corpus 读取：

```text
GET /api/tasks/{task_id}/comments/summary
GET /api/tasks/{task_id}/comments?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&page=1&page_size=20
```

公开字段：

- `comment_id`
- `platform`
- `date`
- `model_name`
- `positive_text`
- `negative_text`
- `full_text`

### 10.3 时间范围报告

```text
POST /api/tasks/{task_id}/time-reports
  -> 校验日期范围
  -> 从 corpus 选择评论
  -> 生成时间版 report
  -> 写 task_artifacts
  -> 返回 report_id
```

时间报告产物：

- 时间版一页纸 Excel。
- 时间版词云 PNG。
- 时间版报告 JSON。
- 时间版 ZIP。

### 10.4 QA

```text
POST /api/tasks/{task_id}/qa
  -> 读取 qa_chunks
  -> 读取 final_report
  -> 检索相关 chunks
  -> LLM 回答或规则 fallback
```

QA 规则：

- 只根据当前任务 evidence 回答。
- 不引入外部资料。
- 证据不足时明确返回 insufficient evidence。
- QA 不改变任务状态。

## 11. Artifact 与结果发布

### 11.1 Artifact 目录

逻辑目录：

```text
ARTIFACT_ROOT/{task_id}/
  staging/
  outputs/raw/
  outputs/postprocess/
  outputs/summary/
  outputs/wordcloud/
  outputs/ai/
  outputs/downloads/
  outputs/time_reports/
  meta/
  logs/
```

### 11.2 Artifact 类型

| 类型 | 用途 |
|---|---|
| raw_workbook | 平台原始口碑导出 |
| validation_json | 采集契约校验 |
| source_status_json | 平台实时、历史、失败状态 |
| postprocess_workbook | 双平台汇总 |
| summary_workbook | 口碑摘要 |
| final_report_json | 主报告 JSON |
| qa_chunks_json | QA evidence |
| wordcloud_png | 词云 |
| keyword_terms_workbook | 词项清单 |
| business_bundle_zip | 业务下载包 |
| time_report_bundle_zip | 时间报告下载包 |

### 11.3 发布规则

```text
write staging artifact
  -> validate required artifact
  -> write task_artifacts
  -> write task_events
  -> update task terminal status
```

发布要求：

- 结果发布必须原子化。
- 未通过校验的 artifact 不进入下载列表。
- 下载包只包含面向业务交付的 Excel、PNG 和报告文件。
- 内部诊断文件只在管理视角展示。

## 12. 数据对象

| 表 | 作用 |
|---|---|
| `tasks` | 任务事实源 |
| `task_vehicles` | 任务内车型、seriesId、平台启用状态 |
| `task_events` | 任务生命周期事件 |
| `collection_runs` | 平台采集 run |
| `collection_run_tasks` | task 与 run 的关系 |
| `collector_events` | collector 状态变化 |
| `task_artifacts` | 任务产物列表 |
| `task_reports` | 主报告和对比报告元数据 |
| `task_time_reports` | 时间范围报告 |
| `task_qa_chunks` | QA evidence 索引 |
| `koubei_raw_comments` | 长期评论语料 |
| `corpus_manifests` | 语料 manifest、known links、cursor |

事实源原则：

- `tasks` 是任务状态事实源。
- `collection_runs` 是平台采集事实源。
- `koubei_raw_comments` 是评论事实源。
- `task_artifacts` 是结果产物事实源。
- GET 接口只读取事实源，不隐式生成新事实。

## 13. 生命周期与访问控制

### 13.1 生命周期

| 数据 | 保留策略 |
|---|---|
| corpus | 长期保留，支持增量采集和降级 |
| task metadata | 长期保留或按归档策略保留 |
| task artifacts | 按业务保留策略清理 |
| collector 临时目录 | 短期清理 |
| logs | 按运维策略清理 |

清理规则：

- corpus 不能被 task artifact 清理任务删除。
- task artifact 清理后，结果页必须展示 artifact 已过期状态。
- 清理动作必须写 `task_events`。
- 下载入口必须以 `task_artifacts` 是否存在为准。

### 13.2 访问控制

角色：

| 角色 | 权限 |
|---|---|
| viewer | 查看工作台、任务、结果和下载可见文件 |
| operator | 创建任务、取消任务、重试失败任务 |
| admin | 发布降级结果、调整并发、清理 artifact、查看诊断文件 |

规则：

- 任务创建需要 operator 或 admin。
- 取消和重试需要 operator 或 admin。
- 发布降级结果需要 admin。
- 诊断日志默认只对 admin 可见。
- 所有写操作必须记录操作者、时间和 action payload。

## 14. 状态与进度边界

任务状态：

```text
queued
running
retry_wait
completed
completed_degraded
failed
cancel_requested
cancelled
expired
```

平台 run 状态：

```text
queued
running
rate_limited
retry_wait
succeeded
failed
cancel_requested
cancelled
timed_out
```

进度边界：

- 本版本不定义新的百分比权重。
- 页面可以展示当前已有状态和 collector run 进度字段。
- ETA 可以继续使用已有字段或阶段统计推导。
- 后续需要单独建立新进度模型，覆盖单车型、双平台、多车型、队列、车道和重试。

## 15. 异常处理与观测

失败类别：

| 类别 | 含义 |
|---|---|
| `rate_limited` | 平台或 OpenClaw 限流 |
| `collector_missing_result` | 采集执行结束但缺少产物 |
| `network_error` | 网络错误 |
| `schema_changed` | 目标站结构变化 |
| `contract_error` | 产物契约不满足 |
| `llm_error` | 报告或 QA 模型调用失败 |
| `artifact_validation_failed` | 产物校验失败 |
| `insufficient_comments` | 可用评论不足 |

观测要求：

- 每个状态变化写 `task_events`。
- 每个 collector 状态变化写 `collector_events`。
- 每个失败必须包含 `failure_category` 和可读 message。
- 工作台总览展示异常任务和当前瓶颈。
- 运维巡检应覆盖容器状态、Temporal worker、collector 服务、OpenClaw Gateway、Postgres 和 artifact 目录。

## 16. 实施顺序

后端：

1. 固化 task-center API 和数据对象。
2. 固化 single workflow。
3. 固化 collector run idempotency。
4. 固化 corpus import/export。
5. 固化 artifact staging -> publish。
6. 固化 comparison workflow。
7. 补齐 comments/time-reports/QA 的 task-native API。
8. 补齐 access control 和 action 审计。

前端：

1. 建立 AppShell、导航、设计 token 和紧凑组件。
2. `/` 默认进入工作台总览。
3. 建立新建任务页。
4. 建立任务中心。
5. 建立任务详情页。
6. 建立结果仪表盘。
7. 接入评论样本、时间报告和 QA。
8. 接入异常、降级和下载状态。

验证：

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
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

数据库巡检：

```bash
sudo docker exec koubei-20260527-postgres-1 \
  psql -U koubei_test -d koubei_test -P pager=off \
  -c "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status;"

sudo docker exec koubei-20260527-postgres-1 \
  psql -U koubei_test -d koubei_test -P pager=off \
  -c "SELECT platform, status, coalesce(failure_category,''), count(*) FROM collection_runs GROUP BY platform,status,failure_category ORDER BY platform,status,failure_category;"
```

## 17. 最终目标态

最终目标态是一个稳定的车型口碑业务工作台：

- 打开即可看到全局负载和可用车道。
- 单车型任务可以双平台并行增量采集。
- 多车型对比可以把 2-5 个车型拆分到可用车道并行执行。
- 用户能看到预计完成时间和当前瓶颈。
- 采集失败时能明确知道是限流、缺产物、网络、schema 变化还是配置问题。
- 降级结果可读、可下载、可追踪。
- 评论样本、时间报告和 QA 全部来自 task-center 和 corpus。
- 结果页直接回答业务问题，而不是只展示文件列表。

