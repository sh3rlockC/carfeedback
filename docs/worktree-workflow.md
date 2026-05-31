# 永久主工作树与功能工作树流程

本文用于把 `carFeedbackv101` 固化为长期主工作树，并在每次新增功能、修复问题或做实验时创建独立 Git worktree。目标是降低单个对话上下文重量，避免在稳定主目录里混杂多个开发任务。

## 目录约定

主工作树固定为稳定入口：

```text
/Users/xyc/Documents/codexwork/carFeedbackv101
```

功能工作树统一放在相邻目录：

```text
/Users/xyc/Documents/codexwork/carFeedbackv101-worktrees
```

推荐结构：

```text
/Users/xyc/Documents/codexwork/
  carFeedbackv101/                 # main，稳定版、部署版、文档入口
  carFeedbackv101-worktrees/
    stability-fixes/                       # 单平台降级、词云兼容等稳定性修复
    admin-console/                         # 后台管理、改口令、取消任务
    agent-pool/                            # 多 agent 并发池
```

## 分支命名

所有功能分支使用 `codex/` 前缀：

```text
codex/stability-fixes
codex/admin-console
codex/agent-pool
codex/cloud-hardening
```

## 创建新功能工作树

在主工作树执行：

```bash
cd /Users/xyc/Documents/codexwork/carFeedbackv101
mkdir -p ../carFeedbackv101-worktrees
git fetch origin
git worktree add ../carFeedbackv101-worktrees/<feature-name> -b codex/<feature-name>
```

示例：

```bash
git worktree add ../carFeedbackv101-worktrees/stability-fixes -b codex/stability-fixes
```

如果分支已存在，用：

```bash
git worktree add ../carFeedbackv101-worktrees/<feature-name> codex/<feature-name>
```

## 新对话启动模板

每次开新上下文时，把下面这段发给模型：

```text
项目路径：/Users/xyc/Documents/codexwork/carFeedbackv101-worktrees/<feature-name>

请先阅读：
- README.md
- docs/next-session-brief.md
- docs/openclaw-skill-flow.md
- docs/worktree-workflow.md

本轮只处理功能：<写清楚本轮目标>。
不要直接改主工作树 /Users/xyc/Documents/codexwork/carFeedbackv101。
如果要提交，提交到当前 codex/<feature-name> 分支。
```

## 开发前检查

进入功能工作树后先执行：

```bash
git status --short --branch
git log --oneline -5
```

如果需要验证基线：

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests -q
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

注意：新 worktree 不会自动带上主目录里的忽略文件，例如 `.env`、`.venv`、`apps/web/node_modules`。如果要在 worktree 里独立运行本地服务，需要复制 `.env` 并安装依赖，或直接走 Docker Compose。

## 本地运行注意事项

不要让多个 worktree 同时占用同一组端口：

```text
80    nginx
3000  Next.js
8000  FastAPI
18790 OpenClaw gateway
5432  Postgres
6379  Redis
```

如果需要并行运行两个版本，请在对应 `.env` 里改端口和数据目录，例如：

```env
HTTP_PORT=8081
JOB_ARTIFACTS_HOST_PATH=./storage/jobs
```

## 完成功能后的合并流程

在功能工作树内：

```bash
git status --short
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests -q
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
git add <changed-files>
git commit -m "<commit message>"
git push -u origin codex/<feature-name>
```

确认功能合并到 `main` 后，在主工作树清理：

```bash
cd /Users/xyc/Documents/codexwork/carFeedbackv101
git worktree remove ../carFeedbackv101-worktrees/<feature-name>
git branch -d codex/<feature-name>
```

## 当前建议的下一批功能工作树

优先级从高到低：

1. `codex/stability-fixes`：修复单平台降级结果页、词云兼容、懂车帝产物校验。
2. `codex/admin-console`：后台改周口令、强制取消任务、查看队列、清理历史任务。
3. `codex/agent-pool`：多用户并发、OpenClaw agent pool、排队位置和任务限流。
4. `codex/cloud-hardening`：HTTPS、备份、日志轮转、生产部署 smoke test。

