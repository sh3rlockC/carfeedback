# 车型口碑情报舱 0-1 复盘

更新时间：2026-04-28

本文用于复盘项目从想法到可部署 Demo 的过程，也可作为简历、项目汇报或后续优化的素材。

## 1. 项目一句话

从 6 个分散的汽车垂媒口碑 skill 出发，搭建一个支持外部链接访问、周口令保护、车型输入、双平台采集、摘要、词云、AI 一页纸、智能问答和结果 ZIP 下载的部门内部 Web Demo，并进一步接入 OpenClaw 作为采集阶段 agent 执行层。

## 2. 初始需求

用户最初需求：

- 从 GitHub 中把汽车垂媒用户评价相关 skill 集合起来。
- 规划一个 Demo。
- 用户输入车型名称后，系统自动采集汽车之家和懂车帝口碑。
- 输出摘要、词云和一页纸。
- 入口可以是微信小程序或网页。

后续约束：

- 部门内部使用，但公司电脑无法自由下载工具。
- 希望通过外部网页访问绕开内网系统流程。
- 不做账号登录，只做链接加访问口令。
- 访问口令每周可自由设定。
- 先做 Web 版。
- 可租云服务器。
- 希望项目体现 AI 结合。

## 3. 关键技术决策

### 3.1 先 Web，后小程序

选择 Web 的原因：

- 部署速度快。
- 可直接链接访问。
- 前后端和异步任务都能用现成 Docker Compose 快速串起来。
- 微信小程序涉及审核、域名备案、小程序端适配和企业分发，不适合 MVP。

### 3.2 单机 Docker Compose

选择单机 Compose 的原因：

- 部门 Demo 初期规模小。
- 服务链路清晰：Nginx + Web + API + Worker + Postgres + Redis。
- 部署和排查成本低。
- 4C8G 云服务器足够支撑 MVP。

### 3.3 Worker 是唯一编排层

设计原则：

- API 只负责请求入口、结果读取和 AI 问答。
- Worker 负责任务队列、阶段顺序、状态、日志、降级和产物校验。
- OpenClaw 不做总控，只作为采集阶段的执行器。

这个决策避免了“Web 后端”和“agent”抢 pipeline 控制权的问题。

### 3.4 OpenClaw 只接采集阶段

最初讨论过所有 skill 都通过 OpenClaw 执行，但最终采用更稳的分层：

- 采集阶段接 OpenClaw，因为采集最像 agent/browser/skill 执行任务。
- 后处理、摘要、词云、AI 一页纸先保持 worker 本地调用，输出更稳定。
- 后续如果某个阶段需要 agent 能力，再逐步迁移。

### 3.5 AI 融入点

AI 不是只挂一个聊天框，而是拆成多个可解释环节：

- OpenClaw agent：负责调用采集 skill。
- AI 一页纸：基于摘要结果生成管理层可读的一页纸解读。
- 智能问答：先检索当前任务证据，再由 LLM 生成回答，并保留审计字段。
- 规则兜底：LLM 不可用时仍能返回确定性答案。

## 4. 0-1 实施阶段

### 阶段 1：项目建模和 Demo 路线

主要产出：

- 明确输入到输出链路。
- 梳理 6 个 skill 的职责。
- 确定 Web 版优先。
- 确定链接加周口令访问方式。
- 初步设计“车型名称 -> 候选确认 -> 采集 -> 结果”的 5 步页面流。

关键思想：

- 先把 pipeline 跑通，不急着重构原 skill。
- 原有 skill 保持独立，Web Demo 通过 wrapper 和 artifact contract 调用。

### 阶段 2：Web MVP

主要产出：

- Next.js 前端。
- FastAPI 后端。
- RQ worker。
- Postgres + Redis。
- Docker Compose。
- 周口令访问。
- 车型输入、候选确认、进度页、结果页。
- 中文文案。

解决的问题：

- 公司电脑无法下载工具：用户只访问网页。
- 不需要账号体系：用每周口令降低访问摩擦。
- 长任务不能阻塞页面：用 Redis/RQ 异步任务和进度轮询。

### 阶段 3：采集和结果产物

主要产出：

- 汽车之家采集。
- 懂车帝采集。
- 双平台摘要 Excel。
- 词云 PNG。
- 词项清单。
- ZIP 结果包下载。
- 两个平台独立进度条。

中间优化：

- 两个平台采集从串行调整为并行。
- 结果页把多个下载入口合并为一个 ZIP。
- 智能问答不显示引用证据，只显示完整回答。
- 前端结果页重新排版，压缩过大的标题和卡片。

### 阶段 4：车型识别优化

遇到的问题：

- 车型查找慢。
- 个别车型 seriesId 识别错误，例如 `风云T9L`、`QQ3`。
- 外网搜索依赖不稳定。

解决思路：

- 已确认车型优先使用缓存，不重复外网搜索。
- 汽车之家搜索策略调整为搜索 `autohome.cn 车型名`，再从候选 URL 中提取 `https://www.autohome.com.cn/{seriesId}/`。
- 懂车帝搜索策略调整为搜索 `dongchedi.com 车型名`，从 `dongchedi.com/auto/series/{seriesId}` 提取 ID。
- 禁止明显错误字段如 `koubei_spec` 被误判为 best candidate。
- 保留手动填写车系 ID 的兜底入口。

### 阶段 5：AI 一页纸和智能问答

主要产出：

- API LLM client。
- DeepSeek V4 Flash 作为批处理 AI 产物模型；AI 一页纸和问答保持 DeepSeek V4 Pro。
- AI 一页纸支持 JSON response format。
- 问答变为“检索当前结果 -> LLM 生成 -> 规则兜底”。
- 问答响应增加审计字段：
  - `answer_source`
  - `model_used`
  - `llm_error`

解决的问题：

- 只靠模板摘要不够“AI”。
- 智能问答必须围绕当前车型结果，而不是泛泛回答。
- LLM 失败时不能让页面不可用。

### 阶段 6：OpenClaw 接入

主要产出：

- 独立 OpenClaw profile：`koubei`。
- 独立 OpenClaw workspace。
- 安装 6 个 skill。
- 配置 `main`、`autohome`、`dongchedi` 三个 agent。
- OpenClaw agent 模型后续切到 DeepSeek V4 Flash，和业务分析模型统一走 DeepSeek API。
- Worker 增加 OpenClaw Gateway adapter。
- 采集阶段路由：
  - `collecting_autohome -> autohome`
  - `collecting_dcd -> dongchedi`
- Worker 继续统一校验 Excel、validation JSON、progress JSON。

重要设计：

- OpenClaw 是执行层，不是编排层。
- `autohome` 和 `dongchedi` 分开，避免同一个 agent 同时跑两个长任务导致上下文和任务状态混乱。
- 两个 agent 各自写自己的产物，worker 等产物。

### 阶段 7：云端部署

主要产出：

- 腾讯云 4C8G Ubuntu 服务器。
- Docker + Docker Compose。
- 项目 rsync 到 `/opt/codexwork`。
- Docker build 使用腾讯云/国内镜像源。
- Nginx 对外监听 `80`。
- OpenClaw systemd 服务。
- OpenClaw gateway token 通过 Docker secret 注入 worker。
- API/worker/web/postgres/redis/nginx 全部 healthy。

解决的问题：

- GitHub clone 因 TLS/网络失败，改用本地 rsync 上传完整 workspace。
- Docker build 国外源慢，改用中国镜像源。
- OpenClaw 不暴露公网端口，只让 worker 通过宿主机访问。

## 5. 主要问题和解决思路

### 5.1 Docker 访问失败

现象：

- 浏览器访问 `127.0.0.1/passphrase` 报 connection refused。

排查：

- 容器未启动或 Nginx 未监听。
- 确认 `docker version`、`docker compose version`。
- 检查 `docker compose ps`、健康检查和端口映射。

解决：

- 确保 Docker Desktop/daemon 启动。
- 用 Compose 启动完整服务。

### 5.2 候选车型为空

现象：

- 页面显示 “No selectable candidates”。

原因：

- 后端没有同时返回汽车之家和懂车帝候选。
- Tavily/API Key 未配置或外网搜索失败。

解决：

- 配置 Tavily。
- 增加手动车系 ID 兜底入口。
- 优化搜索和解析策略。

### 5.3 采集显示 Service Temporarily Unavailable

原因：

- 早期采集依赖 agent-browser 或 agent 执行环境，服务不可用时会卡在采集阶段。

解决：

- 明确 `agent-browser` 不是必须架构，后续决定使用 OpenClaw。
- 将采集阶段通过 OpenClaw agent 执行。

### 5.4 OpenClaw token mismatch

现象：

```text
unauthorized: gateway token mismatch
```

原因：

- OpenClaw 配置中的 `gateway.auth.token` 和 Docker worker 读取的 token 文件不一致。

解决：

- 将 token 文件内容写入 OpenClaw config。
- 重启 `openclaw-koubei.service`。
- 重建/重启 worker。

### 5.5 OpenClaw missing scope operator.write

现象：

```text
INVALID_REQUEST: missing scope: operator.write
```

原因：

- token 能认证，但连接没有设备签名，网关返回 scopes 为空。
- OpenClaw 需要 device identity + 已批准 scopes 才能执行写权限操作。

解决：

- 批准云端设备的 `operator.write`。
- worker 增加 OpenClaw device-auth 握手：
  - 读取 `/openclaw-state/identity/device.json`
  - 使用 Ed25519 私钥签名 OpenClaw v3 payload
  - connect 帧携带 `device`
- 验证 worker 容器连接返回 `operator.write`。

对应提交：

```text
79efeff Add OpenClaw device auth for worker gateway calls
```

### 5.6 两个 OpenClaw agent 并发问题

问题：

- 用户希望汽车之家和懂车帝同时采集。
- 单 agent 同时跑两个长任务容易上下文混乱。

解决：

- 配置两个 agent：
  - `autohome`
  - `dongchedi`
- worker 根据 stage 投递到不同 agent。
- 两边各写自己的 Excel、validation、progress。
- worker 仍统一校验产物。

### 5.7 OpenClaw BOOTSTRAP 拦截 DCD

现象：

- DCD OpenClaw task 显示 succeeded，但没有任何产物。
- session 内容显示 agent 在执行 `BOOTSTRAP.md`，没有调用 DCD skill。

原因：

- workspace 存在 `BOOTSTRAP.md`。
- 新 agent session 首次启动被要求先完成“自我介绍/身份确认”流程。

解决：

- 禁用 `BOOTSTRAP.md`。
- 清空/备份 `dongchedi` 被污染的旧 sessions。
- 做 smoke test 验证不再被拦截。

经验：

- 对生产型 agent workspace，应删除或禁用首次启动 bootstrap。
- OpenClaw task `succeeded` 只代表对话完成，不代表业务产物完成。
- 必须以 artifact contract 作为最终成功标准。

### 5.8 结果页 500

现象：

- 前端显示“结果暂不可用”。
- API 500。

原因：

- DCD 缺失导致摘要是单平台降级格式。
- API result reader 强制读取双平台 sheet `跨平台对比`。

待解决：

- `result_reader.py` 增加 sheet 缺失兼容。
- 单平台降级结果页应正常展示已有摘要和 artifact。

### 5.9 词云失败

现象：

```text
未能从摘要 Excel 中识别到可用词项
```

原因：

- 词云脚本只识别旧双平台摘要 sheet。
- 当前单平台摘要使用新 sheet 名。

待解决：

- 更新 `koubei-wordcloud` 的 `SUMMARY_SHEET_RULES`。
- 或在单平台降级情况下跳过词云并明确提示。

## 6. 工程经验总结

### 6.1 产物 contract 比 agent 成功状态更可靠

Agent 可能“成功回复”，但业务没有完成。最终判断应基于：

- Excel 是否存在。
- validation JSON 是否存在且 ok。
- progress JSON 是否存在且完成。
- 文件是否可被后续阶段读取。

### 6.2 编排层和执行层必须分离

如果让 OpenClaw 也做编排，会出现状态分裂：

- Web 认为任务在哪个阶段？
- agent 认为任务是否完成？
- 数据库如何记录降级？
- 前端如何展示？

最终采用 worker 编排、OpenClaw 执行，降低复杂度。

### 6.3 降级不是失败，但结果页必须兼容降级

本项目后期暴露的问题是：worker 能降级完成，但结果页仍按完整双平台产物读取。后续所有新功能都要问：

- 单平台是否可展示？
- AI 不可用是否可展示？
- 词云失败是否可展示？
- DCD 缺失是否可下载已有结果？

### 6.4 多模型/多供应商配置要分层

本项目有两套模型：

- Web Demo API 的 LLM：批处理使用 DeepSeek V4 Flash，一页纸和问答保持 DeepSeek V4 Pro。
- Hermes/OpenClaw agent 模型：DeepSeek V4 Flash，用于采集和批处理 AI 产物 skill 执行。

两者不应混在一个配置里，否则排障会混乱。

### 6.5 部署时网络环境是核心风险

实际遇到：

- GitHub clone TLS 失败。
- Docker build 访问国外源慢。
- Playwright 浏览器下载困难。

解决思路：

- rsync 上传完整 workspace。
- Dockerfile 使用国内 pip/npm/apt 镜像。
- Playwright 或浏览器依赖不要放在 MVP 的关键路径。

## 7. 简历表达参考

可写成项目经历：

```text
车型口碑情报舱 | 全栈 + Agent 工作流 Demo

设计并实现一个面向部门内部使用的汽车垂媒口碑分析 Web Demo，支持车型输入、汽车之家/懂车帝双平台采集、异步任务进度、摘要 Excel、词云、AI 一页纸、智能问答和 ZIP 结果交付。项目采用 Next.js + FastAPI + RQ + Postgres + Redis + Docker Compose 单机部署，并接入 OpenClaw 作为采集阶段 Agent 执行层。
```

可拆成要点：

- 从 6 个独立 skill 仓库出发，设计统一 artifact contract，将分散脚本整合为可网页触发的稳定流水线。
- 构建 FastAPI + RQ 异步任务系统，支持长采集任务状态追踪、阶段日志、产物校验和降级处理。
- 设计“Worker 编排 + OpenClaw 执行”的 agent 架构，分别使用 `autohome`、`dongchedi` 两个 agent 并行执行双平台采集。
- 接入 DeepSeek V4 Flash 实现 AI 一页纸和基于当前任务证据的智能问答，并设计 LLM 失败的规则兜底和审计字段。
- 完成腾讯云 Ubuntu 单机部署，解决 GitHub TLS、Docker 镜像源、OpenClaw gateway token、device-auth scopes 等部署与集成问题。
- 优化车型识别策略，结合外网搜索、URL 解析、缓存和手动 ID 兜底提升稳定性。
- 重构前端为“车型口碑情报舱”体验，支持 5 步流程导航、双采集进度条、紧凑结果页和一键 ZIP 下载。

可强调的技术难点：

- Agent 成功状态不等于业务成功，最终采用 artifact contract 做业务闭环。
- OpenClaw gateway 需要 device identity 签名和 scope 授权，解决了 `operator.write` 权限问题。
- 在 DCD/汽车之家双采集场景下，使用 stage-specific agent 隔离上下文，避免长任务互相污染。
- 构建可降级 pipeline，保证单平台或 AI 失败时仍可输出可用结果。

## 8. 后续路线图

近期：

- 修 `result_reader.py` 的单平台摘要兼容。
- 修 `koubei-wordcloud` 的新 sheet 兼容。
- 重新跑 QQ3 或风云车型验证 DCD 产物。
- 给结果页增加 degraded 明细。

中期：

- 增加后台任务取消接口。
- 增加队列位置和预计等待时间。
- 增加 job 管理页。
- 增加 OpenClaw agent pool。
- 增加 artifact 自动清理。

长期：

- 接入 HTTPS 和正式域名。
- 增加备份、监控和日志轮转。
- 支持多人并发和权限分组。
- 将车型识别也可选接入 OpenClaw。
- 视需求开发微信小程序壳。
