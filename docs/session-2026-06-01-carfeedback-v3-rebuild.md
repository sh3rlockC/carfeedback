# 2026-06-01 carFeedback V3 重建会话交接

本文记录 2026-06-01 在 `codex/carfeedback-v3-rebuild` 分支完成的服务器并行重建、OpenClaw 8 Agent 池接入、全量采集修复与小米 SU7 验证结果。本文不保存任何 API Key、OpenClaw token、数据库密码或访问 token 明文。

## 1. 部署状态

- 本地 worktree：`/Users/xyc/Documents/codexwork/carFeedbackv101-worktrees/carfeedback-v3-rebuild`
- 服务器新目录：`/opt/codexwork/carFeedback`
- Docker Compose project：`carfeedback-v3`
- 新服务端口：`18080`
- 新服务 base path：`/car-user-feedback`
- 旧 80 服务：`koubei-20260527`，2026-06-01 验证时仍在运行，未停止、未删除。
- OpenClaw gateway：`openclaw-koubei.service`，宿主机端口 `18790`
- OpenClaw agent 池：
  - 汽车之家：`autohome-1`、`autohome-2`、`autohome-3`、`autohome-4`
  - 懂车帝：`dongchedi-1`、`dongchedi-2`、`dongchedi-3`、`dongchedi-4`
  - `main` 仅作为 fallback；旧两个单体 agent 暂未删除。

2026-06-01 验证命令显示：

```text
http://127.0.0.1:18080/healthz -> ok
carfeedback-v3-api/web/worker/temporal-worker/collector/postgres/redis -> healthy
koubei-20260527-nginx-1 -> Up, 0.0.0.0:80->80/tcp
```

## 2. 关键代码提交

- `4fa33f9 Expose task full refresh mode`
  - `/api/tasks` 支持 `collection_mode: "incremental" | "full_refresh"`。
  - 新建任务页新增“增量 / 全量”采集策略控件。
  - Task 列表/详情 schema 返回 `collection_mode`。
  - 旧库启动时补齐 `tasks.collection_mode`。
- `78b2cf3 Enforce full-page OpenClaw collection contracts`
  - full refresh 的 OpenClaw prompt 明确要求自动识别全部页数。
  - 汽车之家 full refresh 禁止 `--end-page 10`，要求 `--auto-detect-pages`。
  - 懂车帝 full refresh 要求 `input.end_page_auto_detected=true`，并在 validation 仍有 `has_more=true` 时失败。
- `3aa5272 Configure collectors for OpenClaw agent pools`
  - 配置汽车之家 4 个 agent、懂车帝 4 个 agent。
- `729efbc Dispatch collection runs through v3 agent pool`
  - collection run 通过 V3 agent 池派发。

## 3. 修复过的问题

### 3.1 full refresh 被隐式限制在 10 页

问题表现：

- 风云 T11 汽车之家一度只采 100 条，实际 API 可到 104 页 / 1032 条。
- 小米 SU7 懂车帝旧路径会出现 10 页左右的采集结果，实际可自动识别到 63 页。

修复：

- 在 OpenClaw prompt 层加入 full refresh command contract。
- 在 worker 输出校验层加入 DCD full refresh guard。
- 针对 DCD 增加 `OPENCLAW_PARTIAL_COLLECTION` 快速失败规则，避免部分采集被当作成功。

### 3.2 `/api/tasks` 无法强制全量

问题表现：

- `/api/tasks` 的数据库模型和 Temporal workflow 已有 `collection_mode`，但创建接口和前端没有暴露。
- 小米 SU7 车系已有历史语料时，普通任务会默认 incremental，无法通过主链路证明全量采集修复。

修复：

- `TaskCreateRequest` 增加 `collection_mode`，默认仍为 `incremental`。
- `create_task()` 写入 `Task.collection_mode`。
- 前端新建任务页增加采集策略分段控件。
- 增加回归测试：传 `collection_mode=full_refresh` 时数据库必须持久化为 `full_refresh`。

## 4. 车型验证结果

### 4.1 风云 T11

任务：`task_20260601_005014_2eaceb`

- 状态：completed，非降级。
- 汽车之家：
  - `review_total=1032`
  - `pages_scanned=104`
  - 最后三页：`102:10`、`103:10`、`104:2`
- 懂车帝：
  - 当前平台实际返回 `150` 条。
  - page 10 `has_more=false`，不是 10 页 cap 导致。

### 4.2 小米 SU7 manual collector full refresh

目录：`/opt/codexwork/carFeedback/storage/jobs/task_manual_xiaomi_su7_full_20260601_0930`

- 汽车之家：
  - `review_total=400`
  - `pages_scanned=40`
- 懂车帝：
  - `total_rows=938`
  - `input.end_page=63`
  - `input.end_page_auto_detected=true`
  - 最后一页：`page=63`、`page_count=11`、`has_more=false`

manual collector 测试中出现过任务目录 root ownership 导致的进度文件写入权限问题，已通过 `chown` 修复该手工测试目录。这个问题不属于页数 cap 修复。

### 4.3 小米 SU7 `/api/tasks` full refresh 主链路

任务：`task_20260601_021155_8a8fd6`

- 状态：`completed`
- 降级：`false`
- 采集模式：`full_refresh`
- 产物数：`12`
- collection runs：
  - `autohome`：`mode=full_refresh`，`status=succeeded`
  - `dongchedi`：`mode=full_refresh`，`status=succeeded`
- 汽车之家 validation：
  - `review_total=400`
  - `pages_scanned=40`
  - 最后三页：`38:10`、`39:10`、`40:10`
- 懂车帝 validation：
  - `total_rows=938`
  - `input.end_page=63`
  - `input.end_page_auto_detected=true`
  - 最后一页：`page=63`、`page_count=11`、`has_more=false`
- 原始产物：
  - `/opt/codexwork/carFeedback/storage/jobs/task_20260601_021155_8a8fd6/outputs/raw/ZJ小米SU7原始口碑.xlsx`
  - `/opt/codexwork/carFeedback/storage/jobs/task_20260601_021155_8a8fd6/outputs/raw/DCD口碑_小米SU7.xlsx`

## 5. 验证命令

本地验证：

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' /Users/xyc/Documents/codexwork/carFeedbackv101/.venv/bin/python -m pytest apps/api/tests apps/worker/tests apps/collector_service/tests -q
npm --prefix apps/web run typecheck
docker compose config --quiet
```

服务器验证：

```bash
curl -fsS http://127.0.0.1:18080/healthz
sudo docker ps --filter 'name=carfeedback-v3' --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo docker ps --filter 'name=koubei-20260527-nginx-1' --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
```

## 6. 后续建议

- 用浏览器检查 `/car-user-feedback/tasks/new` 的“增量 / 全量”控件在桌面和移动端是否符合预期。
- 把 `docs/cloud-deployment.md` 中的旧路径 `/opt/codexwork/carFeedbackv101`、旧 80 单服务描述，在切换主服务前统一改为 V3 并行部署说明。
- 修复汽车之家 OpenClaw progress 百分比运行中可能超过 100 的显示问题。
- 如果确认不再需要旧单体 agent，再执行旧 agent 删除；2026-06-01 会话未删除旧两个 agent。
- 若准备替换 80 端口，先用 18080 的小米 SU7 和风云 T11 结果作为验收基线，再切 Nginx/Compose 入口。
