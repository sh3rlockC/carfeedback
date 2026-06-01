# 车型口碑情报舱项目交接清单

> 2026-06-01 V3 重建状态已更新到 `docs/session-2026-06-01-carfeedback-v3-rebuild.md`。
>
> 当前生产替代版在服务器 `/opt/codexwork/carFeedback` 以 Compose project `carfeedback-v3` 运行，入口端口为 `80`，base path 为 `/car-user-feedback`。旧 80 入口 `koubei-20260527-nginx-1` 已停止；旧项目其他容器仍保留，未删除。
>
> V3 已接入 8 Agent 池：`autohome-1..4` 和 `dongchedi-1..4`；`main` 仅作 fallback。小米 SU7 `/api/tasks` full refresh 主链路已验证完成：汽车之家 40 页 / 400 条，懂车帝 63 页 / 938 条，任务 `task_20260601_021155_8a8fd6` 状态 `completed` 且非降级。
>
> 命名清理已完成：本地主目录为 `/Users/xyc/Documents/codexwork/carfeedback`，远端仓库为 `https://github.com/sh3rlockC/carfeedback.git`。服务器旧带版本后缀目录已重命名为 `/opt/codexwork/carfeedback-legacy-20260601`，不再作为执行目录。

更新时间：2026-06-01

本文用于新模型、新对话或新工程师快速接手 `carfeedback`，重点覆盖当前状态、架构、排查入口、已知问题和后续优化方向。本文不保存任何 API Key、数据库密码、OpenClaw token 或访问口令明文。

## 1. 项目定位

目标：做一个部门内部可通过外部网页访问的汽车垂媒用户口碑 Demo。

核心链路：

```text
输入车型名称
-> 车型识别与候选确认
-> 汽车之家/懂车帝口碑采集
-> 后处理与摘要
-> 词云
-> AI 一页纸
-> 智能问答
-> ZIP 结果包下载
```

当前产品形态：

- Web 版优先，暂未做微信小程序。
- 当前访问方式为开放链接，不做账号登录；周口令门禁暂不恢复。
- 本地和腾讯云单机 Docker Compose 均已跑通基础服务。
- 当前生产替代版在云端通过 Docker Compose project `carfeedback-v3` 监听 `80`；旧 `koubei-20260527` 的 nginx 入口已停止。

## 2. 代码和部署位置

本地项目：

```text
/Users/xyc/Documents/codexwork/carfeedback
```

当前开发 worktree：

```text
/Users/xyc/Documents/codexwork/carfeedback-worktrees/carfeedback-v3-rebuild
```

云端项目：

```text
/opt/codexwork/carFeedback
```

旧目录备份：

```text
/opt/codexwork/carfeedback-legacy-20260601
```

云端 SSH：

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252
```

远端仓库：

```text
https://github.com/sh3rlockC/carfeedback.git
```

历史关键提交：

```text
79efeff Add OpenClaw device auth for worker gateway calls
1d4b354 Use China mirrors for Docker builds
e3625fa Document frontend and OpenClaw deployment state
61f93af Redesign koubei web demo interface
72ba15d Add OpenClaw orchestration and AI result plumbing
```

## 3. 依赖仓库布局

Docker Compose 会把项目父目录挂载到容器 `/workspace`，因此依赖仓库必须和 Web Demo 同处一个 workspace。

云端推荐结构：

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

六个原始 skill：

- `vehicle-id-finder`：车型识别和车系 ID 查找。
- `auto-koubei-collector`：汽车之家口碑采集。
- `dcd-koubei-collector`：懂车帝口碑采集。
- `koubei-keyword-summary-skill`：口碑关键词摘要和一页纸模板。
- `koubei-wordcloud`：词云图和词项清单。
- `koubei-postprocess`：双平台数据后处理。

## 4. 服务架构

Docker Compose 服务：

- `nginx`：对外入口，转发 Web/API，下载 artifact。
- `web`：Next.js 前端。
- `api`：FastAPI 后端，负责访问口令、车型识别、任务创建、结果读取、AI 一页纸、智能问答。
- `worker`：RQ worker，负责异步流水线、阶段编排、产物校验、降级处理。
- `postgres`：任务、产物、AI 报告、QA chunk 持久化。
- `redis`：任务队列。

OpenClaw 服务：

- 云端 systemd 服务：`openclaw-koubei.service`
- profile：`koubei`
- gateway：宿主机 `127.0.0.1:18790`
- worker 容器访问：`ws://host.docker.internal:18790`
- agent：
  - `main`：默认兜底。
  - `autohome-1`、`autohome-2`、`autohome-3`、`autohome-4`：汽车之家采集池。
  - `dongchedi-1`、`dongchedi-2`、`dongchedi-3`、`dongchedi-4`：懂车帝采集池。

运行原则：

- Worker 是唯一总编排层。
- OpenClaw 只作为采集阶段执行层。
- 当前 OpenClaw 阶段：`collecting_autohome,collecting_dcd`。
- 摘要、词云、AI 一页纸、问答仍在 worker/API 内执行。

## 5. 当前云端状态

2026-06-01 核对状态：

- Docker 服务：`carfeedback-v3-api/web/worker/temporal-worker/collector/postgres/redis` 均 healthy。
- 当前入口：`http://127.0.0.1/healthz` 返回 `ok`，`http://129.211.223.252/car-user-feedback/tasks` 和 `/car-user-feedback/api/tasks?limit=1` 返回 200。
- 旧 80 入口：`koubei-20260527-nginx-1` 已停止；旧项目其他容器仍保留，未删除。
- OpenClaw systemd：active。
- OpenClaw gateway：宿主机端口 `18790`。
- OpenClaw agent 池：`autohome-1..4`、`dongchedi-1..4` 已接入；`main` 仅 fallback。
- 小米 SU7 主链路 full refresh 已验证：汽车之家 40 页 / 400 条，懂车帝 63 页 / 938 条，任务完成且非降级。

注意：服务器 `.runtime` 保存 runtime secrets，部署同步必须排除 `.runtime`，不要从本地覆盖。

## 6. 环境变量重点

`.env` 不要提交。关键变量：

```env
APP_ENV=production
BASE_URL=http://服务器IP或域名/car-user-feedback
HTTP_PORT=80
BACKEND_ORIGIN=http://api:8000

DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://redis:6379/0

ACCESS_CONTROL_ENABLED=false
NEXT_PUBLIC_ACCESS_CONTROL_ENABLED=false

TAVILY_API_KEY=...

LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_REPORT=deepseek-v4-flash
LLM_MODEL_QA=deepseek-v4-flash

OPENCLAW_ADAPTER_ENABLED=true
OPENCLAW_ADAPTER_STAGES=collecting_autohome,collecting_dcd
OPENCLAW_GATEWAY_URL=ws://host.docker.internal:18790
OPENCLAW_GATEWAY_TOKEN_FILE=/run/secrets/openclaw_gateway_token
OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2,autohome-3,autohome-4
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4
OPENCLAW_ARTIFACT_ROOT_HOST=/opt/codexwork/carFeedback/storage/jobs
OPENCLAW_TASK_DB_PATH=/openclaw-state/tasks/runs.sqlite
OPENCLAW_DEVICE_IDENTITY_FILE=/openclaw-state/identity/device.json
```

OpenClaw agent 模型和 API Key 不在 Web Demo `.env` 中管理，位于 OpenClaw profile/agent 状态目录中。

## 7. 当前已完成能力

产品：

- 开放访问入口。
- 车型输入。
- 汽车之家/懂车帝候选确认。
- 候选失败时支持手动填写车系 ID。
- 任务进度页。
- 汽车之家/懂车帝独立采集进度条。
- 结果页。
- ZIP 下载全部结果。
- AI 一页纸。
- 智能问答。
- 智能问答审计字段：`answer_source`、`model_used`、`llm_error`。
- 前端中文化和“车型口碑情报舱”视觉重设计。

工程：

- Docker Compose 单机部署。
- 腾讯云 4C8G Ubuntu 部署。
- 中国镜像源加速 Docker build。
- OpenClaw 独立 systemd 部署。
- OpenClaw `autohome` / `dongchedi` 双 agent 路由。
- Worker OpenClaw Gateway adapter。
- Worker OpenClaw device-auth 握手修复。
- RQ + Redis 异步任务。
- Postgres 任务、产物、AI 报告、QA chunk 表。
- API 结果组装与 artifact 下载。
- 采集产物 contract 校验。
- 阶段失败降级策略。

## 8. 历史问题和当前风险

以下 `8.1` 到 `8.3` 是 2026-04-28 的历史问题记录，用于理解旧链路故障。2026-06-01 V3 重建后，DCD bootstrap 拦截和单平台降级并不是当前小米 SU7 full refresh 主链路的阻塞项；当前接手应以 `docs/session-2026-06-01-carfeedback-v3-rebuild.md` 的测试结果为准。

### 8.1 最新 QQ3 任务 DCD 无产物

最新问题 job：

```text
job_20260428_041230_3c80c6
```

现象：

- 汽车之家成功，72 条口碑。
- 懂车帝阶段 `degraded`。
- 缺少 `DCD口碑_QQ3.xlsx`、`DCD口碑_QQ3.validation.json`、`collecting_dcd.progress.json`。

根因：

- `dongchedi` OpenClaw agent 被 workspace `BOOTSTRAP.md` 拦截。
- agent 没有执行 `dcd-koubei-collector`，而是回复“Bootstrap 还未完成，请先告诉我怎么称呼你”。
- OpenClaw 把这次对话标记为 succeeded，worker 只能通过缺失产物识别为降级。

已处理：

- 云端禁用 `BOOTSTRAP.md`。
- 备份并清空 `dongchedi` 旧 sessions。
- smoke test 已证明不再 bootstrap 拦截。

待验证：

- 重新跑一个新任务，确认 DCD 真实产物生成。

### 8.2 单平台降级结果页 500

现象：

- DCD 缺失后，摘要 Excel 是单平台降级摘要。
- 前端结果页显示“结果暂不可用”。
- API `/api/jobs/{job_id}/result` 500。

直接错误：

```text
KeyError: Worksheet 跨平台对比 does not exist.
```

根因：

- `apps/api/app/services/result_reader.py` 强制读取 `跨平台对比`、`综合业务摘要`、`一页纸总结` 等双平台 sheet。
- 单平台摘要 Excel 的 sheet 名是 `总览摘要`、`业务摘要`、`产品机会点`、`最满意方向统计`、`最不满意方向统计` 等。

待修：

- `result_reader.py` 应支持缺失 sheet 返回空列表。
- `综合业务摘要` 应兼容 `业务摘要`。
- `一页纸总结` 缺失时可从 `总览摘要` 或 `业务摘要` 生成 fallback 文案。

### 8.3 单平台摘要词云失败

现象：

```text
ERROR: 未能从摘要 Excel 中识别到可用词项
```

根因：

- `koubei-wordcloud/scripts/wordcloud_utils.py` 的 `SUMMARY_SHEET_RULES` 只识别旧双平台 sheet：
  - `汽车之家_满意摘要`
  - `汽车之家_不满意摘要`
  - `懂车帝_正向摘要`
  - `懂车帝_负向摘要`
- 当前单平台摘要 Excel 使用：
  - `最满意方向摘要`
  - `最不满意方向摘要`
  - `最满意高频词`
  - `最不满意高频词`

待修：

- 给 `koubei-wordcloud` 增加新 sheet 名兼容。
- 或在 worker 对单平台摘要跳过词云并明确降级，不影响结果页展示。

## 9. 排查优先级

新对话接手时建议按这个顺序查：

1. 先确认服务状态：

```bash
cd /opt/codexwork/carFeedback
sudo docker compose -p carfeedback-v3 ps
systemctl is-active openclaw-koubei.service
curl -fsS http://127.0.0.1/healthz
curl -fsS http://129.211.223.252/car-user-feedback/tasks
curl -fsS http://127.0.0.1:18790/healthz
```

2. 查最新 job：

```bash
sudo docker compose -p carfeedback-v3 exec -T postgres psql -U koubei -d koubei -c \
"SELECT job_id, query, model_name, status, current_stage, degraded, created_at, started_at, finished_at FROM jobs ORDER BY created_at DESC LIMIT 10;"
```

3. 查阶段状态：

```bash
sudo docker compose -p carfeedback-v3 exec -T postgres psql -U koubei -d koubei -c \
"SELECT stage_name,status,error_code,left(coalesce(error_message,''),500),started_at,ended_at FROM job_stage_runs WHERE job_id='JOB_ID' ORDER BY started_at;"
```

4. 查产物：

```bash
find storage/jobs/JOB_ID -maxdepth 6 -type f -printf "%T@ %s %p\n" | sort -nr | sed -n "1,200p"
```

5. 查 worker/API 日志：

```bash
sudo docker compose -p carfeedback-v3 logs --tail=200 worker
sudo docker compose -p carfeedback-v3 logs --tail=200 api
```

6. 查 OpenClaw task：

```bash
openclaw --profile koubei tasks list --json
```

7. 查对应 agent session：

```bash
find /home/ubuntu/.openclaw-koubei/agents/dongchedi-1/sessions -maxdepth 1 -type f -printf "%T@ %s %p\n" | sort -nr | head
find /home/ubuntu/.openclaw-koubei/agents/autohome-1/sessions -maxdepth 1 -type f -printf "%T@ %s %p\n" | sort -nr | head
```

8. 如果 DCD 或汽车之家 OpenClaw 阶段“succeeded 但无产物”，优先查：

- 是否被 `BOOTSTRAP.md` 拦截。
- agent 是否只是回复文字，没有调用 `exec`。
- prompt 中 output_path 是否是宿主机路径。
- 对应目录是否可写。
- skill 脚本是否存在且依赖齐全。

## 10. 常用本地命令

```bash
cd /Users/xyc/Documents/codexwork/carfeedback
docker compose ps
docker compose logs --tail=200 worker
docker compose logs --tail=200 api
.venv/bin/python -m pytest -q apps/worker/tests
```

前端：

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

OpenClaw：

```bash
openclaw --profile koubei agents list --json
openclaw --profile koubei skills list
openclaw --profile koubei tasks list --json
openclaw --profile koubei gateway status
```

## 11. 下一步建议

短期：

1. 用浏览器检查 `/car-user-feedback/tasks/new` 的“增量 / 全量”控件在桌面和移动端的实际效果。
2. 修复汽车之家 OpenClaw progress 百分比运行中可能超过 100 的显示问题。
3. 保留旧两个单体 agent 一段时间；确认不再需要后再删除。
4. 观察 80 入口稳定性，再单独规划旧项目剩余容器、旧 volumes 和旧 artifact 的清理窗口。

中期：

1. 给 OpenClaw accepted task 增加“必须至少有一次工具调用或必须写 progress_file”的检测。
2. 对 DCD/汽车之家采集增加更明确的失败日志和前端可见诊断。
3. 增加多用户并发队列提示、任务取消按钮和后台强制取消接口。
4. 为结果页增加 degraded banner，清楚说明哪个平台缺失、哪些功能降级。

长期：

1. 如果多人稳定使用，考虑拆分 worker 和 OpenClaw 到独立服务器或至少独立资源限制。
2. 把车型识别也迁入 OpenClaw `vehicle-id-finder`，但保留缓存和手动兜底。
3. 加 HTTPS、域名、备份、日志轮转和 artifact 清理策略。
4. 规划 80 端口切换和旧服务清理窗口。

## 12. 不要做的事

- 不要提交 `.env`、token、API Key、数据库密码、OpenClaw auth 文件。
- 不要重新启用 `BOOTSTRAP.md` 到生产 OpenClaw workspace。
- 不要把 OpenClaw 当成总编排层；worker 仍是唯一 pipeline owner。
- 不要只看 OpenClaw task `succeeded` 就判定采集成功；必须校验 Excel、validation JSON、progress JSON。
- 不要在未确认路径映射时修改 artifact_root。
