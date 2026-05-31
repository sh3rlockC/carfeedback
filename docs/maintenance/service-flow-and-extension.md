# carFeedbackv101 服务流程与扩展接口

本文用于维护 `carFeedbackv101` 的线上服务，记录本次修复后的运行策略、数据流、Temporal 边界和后续扩展入口。

## 当前策略

- 访问策略：保留开放访问，不恢复周口令门禁。
- 对外入口：只暴露 `/car-user-feedback/` 和 `/car-user-feedback/api/`，裸 `/api/` 由 Nginx 返回 `404`。
- 任务创建：单车型和对比任务都必须带已确认的 `series_id` 候选，且每个启用平台都要确认。
- 结果读取：`GET /api/jobs/{job_id}/result` 只读取已有 AI report、QA chunks 和 artifact，不在 HTTP 请求中同步调用 LLM。
- 自动重试：`pause-retry` 在 worker 调度自动重试前生效，暂停后不再创建新的 retry run。

## 系统架构

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
  -> storage/jobs + storage/corpus
```

主要服务：

- `web`：Next.js UI，负责车型输入、候选确认、任务列表、进度和结果页。
- `api`：FastAPI，负责候选解析、任务写入、任务状态读取、artifact 下载和只读结果装配。
- `temporal-worker`：运行 `SingleVehicleTaskWorkflow` 和 `ComparisonTaskWorkflow`。
- `autohome-collector`、`dongchedi-collector`：平台采集服务，提供 `/runs` 接口。
- `postgres`：保存任务、车辆、collection runs、events、artifacts、AI report、QA chunks。
- `redis`：保留队列兼容能力，主链路以 Temporal 为准。

## 任务创建到产物发布

1. Web 调用 `POST /api/vehicles/resolve` 获取汽车之家和懂车帝候选。
2. Web 对每辆车调用 `POST /api/vehicles/validate-series` 校验手动或自动候选。
3. Web 调用 `POST /api/tasks` 创建任务，`vehicles[].selected_candidates` 必须包含启用平台的 confirmed `series_id`。
4. API 写入 `tasks`、`task_vehicles`、`task_events`，并启动对应 Temporal workflow。
5. Worker 创建或复用 `collection_runs`，分平台调度 collector service。
6. Collector 写入原始采集文件、progress JSON 和 resume cursor。
7. Worker 导入语料、导出平台 workbook、执行后处理、Hermes/LLM 摘要、词云和一页纸报告。
8. Worker 写入 `task_artifacts`、更新任务 terminal 状态并发布 `task_published` 或 `comparison_published` 事件。
9. Web 通过 `GET /api/tasks/{task_id}`、artifact URL 和旧 job result 兼容接口读取产物。

## 单车型数据流

- API 输入：`task_type=single_vehicle`，只允许 1 辆车。
- `task_vehicles` 保存 `query`、`model_name`、`autohome_series_id`、`dcd_series_id`、`enabled_platforms`。
- Worker 按平台生成或复用 `collection_runs`，同一 `query_key + platform + series_id` 可被多个 task 共享。
- 平台采集成功后，worker 继续生成统一产物；若只有部分平台失败，会进入 degraded 发布，并按策略调度自动重试。
- 用户调用 `POST /api/tasks/{task_id}/pause-retry` 后，`is_retry_paused` activity 会阻止后续自动重试。

## 对比任务数据流

- API 输入：`task_type=comparison`，至少 2 辆车；每辆车都要求完整 confirmed candidates。
- 新对比任务以 `tasks/task_vehicles` 为事实源，不再依赖 `comparison_jobs/comparison_vehicles`。
- `ComparisonTaskWorkflow` 对 `task_` id 直接读取 `TaskStore.load_task()`，为每辆车创建或复用 child single task。
- child task id 写回父任务 `task_vehicles.result_snapshot_json.child_task_id`，同时更新车辆状态。
- 对比发布时，worker 从 child task 的可用产物构建比较报告和 ZIP，并写入父任务 `task_artifacts`。
- 旧 `cmp_` 对比路径保留兼容：如果传入旧 comparison id，仍读取 `comparison_jobs/comparison_vehicles`。

## Temporal Activity 边界

单车型核心边界：

- `load_task`：从 `tasks/task_vehicles` 读取任务快照。
- `resolve_vehicle_inputs`：构建平台采集参数。
- `create_or_join_collection_run`：创建或复用平台采集 run。
- `wait_for_collection_runs`：轮询 collector service 并同步进度。
- `import_run_rows_to_corpus`：导入评论语料。
- `export_vehicle_workbooks`：导出平台 workbook。
- `run_postprocess`：生成跨平台后处理产物。
- `run_llm_report`：生成 Hermes/AI 相关产物。
- `publish_degraded_result`、`publish_full_result`：发布 terminal 状态和 artifacts。
- `schedule_retry`、`retry_failed_platforms`、`is_retry_paused`：自动重试控制。

对比核心边界：

- `load_comparison_task`：读取 native task 或 legacy comparison。
- `ensure_vehicle_subworkflow`：为每辆车创建或复用 child single task。
- `wait_for_vehicle_results`：等待 child task/job terminal。
- `generate_comparison_report`：生成对比报告。
- `mark_comparison_comparing`：标记比较阶段。
- `publish_comparison_result`：发布对比 task artifacts 和状态。
- `regenerate_comparison_after_upgrade`：child task 升级为 full 后重新生成对比。

Activity 只接收可序列化 dict，Workflow 不直接访问数据库、文件系统或 collector HTTP。

## Collector 接口

Collector service 暴露：

- `GET /healthz`：健康检查。
- `POST /runs`：创建或复用采集 run。
- `GET /runs/{run_id}`：读取 run 状态和进度。
- `POST /runs/{run_id}/cancel`：请求取消。

`POST /runs` 请求字段：

- `run_id`：worker 分配的 collection run id。
- `task_id`：归属 task id，用于产物目录和进度文件。
- `platform`：`autohome` 或 `dongchedi`。
- `query_key`、`model_name`、`series_id`：车型定位信息。
- `mode`：`full_refresh`、`incremental`、`retry`、`backfill`。
- `known_links`、`resume_cursor`：增量和断点续跑输入。
- `max_scan_pages`、`stop_after_known_pages`：采集边界。

Collector terminal 状态：

- `succeeded`：必须返回 `output_path` 或 artifact paths，且文件实际存在。
- `failed`：必须提供 `failure_category`，worker 会映射到任务失败或 degraded 状态。
- `cancelled`：用户或 worker 取消。

## Artifact 类型

常见产物：

- 原始平台 Excel：汽车之家、懂车帝原始口碑。
- validation JSON：采集器输出校验信息。
- progress JSON：`progress/collecting_autohome.progress.json`、`progress/collecting_dcd.progress.json`。
- corpus JSONL：标准化评论语料。
- postprocess Excel/JSON：跨平台合并和指标产物。
- Hermes/AI JSON：`final_report.json`、QA chunks、事实文件。
- wordcloud PNG/Excel：正向、负向词云和词表。
- comparison report/ZIP：对比报告和下载包。

API result 读取规则：

- 优先读 `job_ai_reports`，其次读已有 `final_report.json` artifact。
- QA 只判断已有 `job_qa_chunks` 或 `qa_chunks.json` artifact。
- 不在 GET 请求中补生成 AI report 或 QA chunks。

## 状态与错误

任务状态：

- `queued`、`running`、`retry_wait`：非终态。
- `completed`：完整成功。
- `completed_degraded`：部分平台失败但已有可发布产物。
- `failed`：无可发布结果或关键 artifact 缺失。
- `cancelled`：用户取消。

常见 failure category：

- `timeout`：采集或阶段超时。
- `network_error`：网络错误。
- `rate_limited`：目标站或代理限流。
- `collector_missing_result`：collector 声称完成但 artifact 缺失。
- `schema_changed`：目标站结构或输出契约变化。
- `config_error`：环境变量、依赖或路径配置错误。
- `agent_busy`：可选 OpenClaw/agent 池繁忙。
- `worker_error`：未分类 worker 异常。

## 部署检查

重命名后的运行目录：

```bash
cd /Users/xyc/Documents/codexwork/carFeedbackv101
```

上线前检查：

- `.env` 中 `NEXT_PUBLIC_BASE_PATH=/car-user-feedback`。
- `.env` 中 `ACCESS_CONTROL_ENABLED=false` 和 `NEXT_PUBLIC_ACCESS_CONTROL_ENABLED=false`。
- `docker compose config --quiet` 通过。
- Nginx 只代理 `/car-user-feedback/api/`，裸 `/api/` 返回 `404`。
- `docker compose up -d --build` 后 `docker compose ps` 核心服务 healthy。
- 手动创建单车型任务和 2 车型对比任务。
- 对比任务缺 candidates 时 `POST /api/tasks` 返回 `400`。
- `/api/jobs/{id}/result` 在没有 AI/QA 产物时返回可用基础结果，不触发 LLM。

## 后续扩展接口

新增平台：

- API schema 增加平台候选字段和 `enabled_platforms` 校验。
- `task_vehicles` 增加平台 series id 或改成平台候选子表。
- Worker 增加 platform collector client、collection run 分派和 artifact 校验。
- Web 增加候选确认 UI，不允许跳过 confirmed `series_id`。

新增 artifact：

- Worker 在发布阶段写入 `task_artifacts`，设置 `artifact_type`、`downloadable`、`mime_type`。
- API 只做读取和下载，不在 GET 中补计算。
- Web 通过 task detail 的 artifact list 渲染入口。

新增分析阶段：

- 优先新增 Temporal activity，并让 workflow 只传递 dict payload。
- 阶段输出必须有稳定文件名和 JSON contract。
- 阶段失败要映射到明确 `failure_category`，避免只暴露原始异常。

恢复周口令门禁时需要修改：

- API：`ACCESS_CONTROL_ENABLED=true`、`PASS_PHRASE_HASH=sha256:<hex>`、`PASS_PHRASE_VERSION=<version>`、`SESSION_SECRET=<random>`。
- Web build/runtime：`NEXT_PUBLIC_ACCESS_CONTROL_ENABLED=true`，需要重新 build web 镜像。
- 验证项：无 cookie 访问 `/api/tasks` 返回 `401`；`POST /api/access/verify` 正确口令返回 cookie；错误口令返回 `401`；已有 task view/manage token 语义不被破坏。

OpenClaw 作为可选扩展：

- 默认 compose 不启用 OpenClaw，也不挂载 OpenClaw secret。
- 需要启用时再设置 `OPENCLAW_ADAPTER_ENABLED=true`、`OPENCLAW_ADAPTER_STAGES`、agent id、gateway URL、token file 和 state path。
- 启用前确认 artifact 根目录映射一致，collector 输出契约仍由 worker 校验。
