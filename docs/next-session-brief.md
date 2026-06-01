# 车型口碑 Web Demo 下一步交接卡

> 2026-06-01 最新接手入口：
>
> - `docs/session-2026-06-01-carfeedback-v3-rebuild.md`
> - 远端仓库：`https://github.com/sh3rlockC/carfeedback.git`
> - 本地主目录：`/Users/xyc/Documents/codexwork/carfeedback`
> - 本地 worktree：`/Users/xyc/Documents/codexwork/carfeedback-worktrees/carfeedback-v3-rebuild`
> - 服务器新目录：`/opt/codexwork/carFeedback`
> - 服务器旧带版本后缀目录已重命名为 `/opt/codexwork/carfeedback-legacy-20260601`
> - 新并行服务：Compose project `carfeedback-v3`，端口 `18080`，base path `/car-user-feedback`
> - 旧 80 服务：`koubei-20260527`，2026-06-01 验证时仍在运行，未停止。
>
> 下方 2026-04-27 内容是早期历史上下文，涉及 `/opt/codexwork/carFeedback`、80 端口单服务、单体 `autohome` / `dongchedi` agent 的描述不能作为 V3 重建现状使用。

> 2026-04-28 后续接手请优先阅读：
>
> - `docs/project-handoff-checklist.md`
> - `docs/project-0-to-1-retrospective.md`
> - `docs/worktree-workflow.md`
>
> 本文件保留早期交接上下文，部分状态已被上述新文档覆盖。后续新增功能应优先在独立 worktree 中进行，主工作树仅作为稳定入口和部署基线。

更新时间：2026-04-27

## 当前可用状态

- 分支：`main`
- remote：`origin https://github.com/sh3rlockC/carfeedback.git`
- 主工作树：`/Users/xyc/Documents/codexwork/carfeedback`
- 功能 worktree 根目录：`/Users/xyc/Documents/codexwork/carfeedback-worktrees`
- 历史关键提交：
  - `1dafe31 Initial vehicle koubei demo`
- 本地 Web Demo 已通过 Docker Compose 运行：
  - `nginx` 对外监听 `http://127.0.0.1/`
  - `web/api/worker/postgres/redis` 均为 healthy
  - 本周本地访问口令已设为 `123456`
- 最新人工回归：
  - `job_20260427_073149_77861c`
  - 车型：`风云T9L`
  - 状态：`completed`
  - 降级：`false`
  - 用户已确认：ZIP 下载、AI 一页纸、智能问答审计字段均正常。
- 项目专用 OpenClaw 已独立部署：
  - profile：`koubei`
  - 本机地址：`http://127.0.0.1:18790/`
  - 容器内访问地址：`http://host.docker.internal:18790/`
  - LaunchAgent：`ai.openclaw.koubei`
  - 状态目录：`~/.openclaw-koubei`
  - 本地 runtime 目录：`/Users/xyc/Documents/codexwork/openclaw-koubei-runtime/`
  - 已配置 agent：`main`、`autohome`、`dongchedi`
  - `autohome`、`dongchedi` 的 auth/model 配置已同步自原 `main` agent；旧 session 已备份清空，runtime workspace 的 `BOOTSTRAP.md` 已移除，避免新 agent 被初始化流程拦截。
- 云服务器部署清单已写好：`docs/cloud-deployment.md`

## 已完成能力

- 访问口令入口、车型输入、候选确认、任务进度、结果页。
- 前端可见文案已中文化。
- 任务进度页已增加汽车之家、懂车帝两个采集阶段独立 0-100 进度条，数据来自各自 `.progress.json`。
- 车型识别无结果时支持手动填写汽车之家和懂车帝车系编号继续创建任务。
- 结果页已预留智能一页纸和问答展示入口。
- 结果页已合并下载入口：一个 ZIP 下载按钮打包 Excel 结果和词云 PNG。
- 智能问答已改为“任务证据检索 + LLM 生成回答 + 规则兜底”，前端不显示引用证据。
- 智能问答响应已增加审计字段并在前端展示：
  - `answer_source`: `llm` 或 `fallback`
  - `model_used`: 实际使用的 QA 模型
  - `llm_error`: LLM 未配置、空返回、异常或证据不足原因
- 前端已完成“车型口碑情报舱”重设计，包含全局任务指挥条、5 步流程导航、双采集 lane、紧凑结果封面、ZIP 交付条和全宽 QA 区。
- API/worker 已有任务、产物、问答索引和结果读取基础结构。
- Docker 容器可访问项目专用 OpenClaw gateway。
- Worker 已有 OpenClaw Gateway adapter：定位为采集阶段执行层，当前本机已启用 `collecting_autohome,collecting_dcd`，并继续由 worker 校验 Excel、validation JSON 和 progress JSON 产物。
- Worker 已支持阶段级 OpenClaw agent 路由：`collecting_autohome -> agent_id=autohome`，`collecting_dcd -> agent_id=dongchedi`；两边各写自己的 Excel、validation JSON 和 progress JSON，worker 继续统一校验。
- 六个口碑 skill 已安装到 `koubei` OpenClaw workspace，并被 OpenClaw 识别为 `Ready`：`vehicle-id-finder`、`auto-koubei-collector`、`dcd-koubei-collector`、`koubei-postprocess`、`koubei-keyword-summary`、`koubei-wordcloud`。
- OpenClaw 装上后的流程已整理在 `docs/openclaw-skill-flow.md`。
- API 已有 LLM client：OpenAI-compatible 和 Anthropic-compatible messages 均支持；当前本机一页纸报告和智能问答配置为 DeepSeek V4 Flash，走 OpenAI-compatible `/chat/completions`；一页纸对 DeepSeek 启用 JSON response format，失败或返回不合法时保留确定性模板降级。
- `koubei` OpenClaw profile 默认模型已切回 `minimax-portal/MiniMax-M2.7`，Docker worker 到 gateway 的 WebSocket 握手已验证通过。
- Nginx 已对 `/api/jobs/<job_id>/progress` 进度轮询端点豁免通用 API 限流，其它 `/api/` 仍保留限流。
- Worker 写入 Postgres `degraded` Boolean 字段时已改为原生 bool 参数，避免 `smallint -> boolean` 类型错误。
- Worker 词云阶段已显式传入 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`，新验证任务已生成优点/槽点词云 PNG 和词项 Excel。

## 执行路由原则

- Worker 是总编排层：负责队列、阶段顺序、数据库状态、降级策略、日志、进度和产物校验。
- OpenClaw 是采集阶段的可插拔执行层：只执行 `OPENCLAW_ADAPTER_STAGES` 中显式列出的采集阶段。
- 未列入 `OPENCLAW_ADAPTER_STAGES` 的阶段继续走原 worker runner，不与 OpenClaw 抢控制权。
- 当前本机配置已启用 `collecting_autohome,collecting_dcd`；两个采集阶段走 OpenClaw。
- 两个采集阶段分别投递到不同 OpenClaw agent；如果未配置阶段级 agent ID，则回落到 `OPENCLAW_AGENT_ID`。
- OpenClaw 长任务采用“提交后轮询产物”模式：Gateway 接单后，worker 等待 expected artifacts，不再同步等待 Gateway 最终响应。
- Worker 已增加 OpenClaw task DB 状态检查：如果后台 task 已经 `failed/timed_out/cancelled/lost`，不再等满产物超时。
- 摘要、词云和后处理也已安装在 OpenClaw workspace，但当前流程暂不通过 OpenClaw 执行，继续走 worker。

## 未完成 / 阻塞点

- OpenClaw gateway 已具备 `operator.write`；云端部署时仍需重新配置 token、权限、模型配置和 agent 状态目录。
- 真实任务已验证两个采集阶段都通过 OpenClaw 产出 Excel、validation JSON 和 progress JSON。
- OpenClaw agent 执行不会天然写现有进度文件；新 skill 或新阶段接入时需要继续遵守当前 `.progress.json` 产物 contract。
- Tavily 已配置，车型识别可调用外部搜索；仍建议保留已知车型映射和缓存以降低外部依赖。
- 云端尚未实际部署：还没有服务器、域名、HTTPS、生产 `.env` 和备份策略落地。
- 当前只适合小规模多人使用；如果部门多人并发，需要扩展 worker 并发、OpenClaw agent pool、任务排队提示和产物清理策略。

## 下一步执行顺序

1. 固化当前版本：
   - 拆分并提交当前本地改动。
   - 避免提交 `.env`、`storage/jobs`、`.next`、`node_modules`、OpenClaw token 和本地 runtime。
2. 处理真实采集问题：
   - 如 OpenClaw 阶段失败，优先查看对应阶段的 `*.openclaw.stdout.log` 和 `*.openclaw.stderr.log`。
   - 确认采集 skill 写出的路径与 `OPENCLAW_ARTIFACT_ROOT_HOST`、`ARTIFACT_ROOT` 映射一致。
   - 确认 `.progress.json`、`.validation.json` 和 Excel 均被 worker 校验通过。
3. 云端部署：
   - 按 `docs/cloud-deployment.md` 租服务器并部署。
   - 配置域名、HTTPS、生产口令、密钥、数据库密码和备份。
   - 决定 OpenClaw 在云端是宿主机服务还是独立容器服务。
4. 多人并发增强：
   - 增加排队位置和预计等待时间。
   - 规划 `autohome-*`、`dongchedi-*` agent pool。
   - 增加单用户并发限制和产物自动清理策略。

## 常用命令

```bash
cd /Users/xyc/Documents/codexwork/carfeedback
docker compose ps
docker compose exec nginx nginx -t
docker compose logs --tail=200 worker
docker compose logs --tail=200 api
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests -q
```

```bash
openclaw --profile koubei gateway status
openclaw --profile koubei gateway restart
curl http://127.0.0.1:18790/healthz
```

## 注意事项

- 不要提交 `.env`、OpenClaw token、日志或本地 runtime 目录。
- 当前根工作区还有与本项目无关的未提交改动，继续本项目时不要误改或误提交。
- 如果要安装 GitHub skill，必须先跑 `skill-security-auditor`；风险评分大于 40 不建议安装。
