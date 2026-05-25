# 云服务器部署清单与上线步骤

本文面向准备租用云服务器上线 `vehicle-koubei-web-demo` 的用户。当前项目是单机 Docker Compose 部署：Nginx 对外提供 Web 入口，后端 API、worker、Postgres、Redis 在同一台服务器内运行。

## 推荐服务器配置

最低可用配置：

- 2 核 CPU
- 4 GB 内存
- 60 GB SSD 系统盘
- Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS
- 公网 IPv4

推荐上线配置：

- 4 核 CPU
- 8 GB 内存
- 100 GB 以上 SSD 系统盘
- Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS
- 公网 IPv4
- 按量或包年带宽不低于 5 Mbps

如果需要长期保留采集结果、原始评论语料、日志、词云图片和 AI 报告，建议额外挂载数据盘，并定期备份 Docker volumes 与 `storage/corpus`。

## 域名与端口要求

对外访问入口由 `nginx` 容器提供：

- 默认监听服务器 `80` 端口，对应 `.env` 中的 `HTTP_PORT=80`
- 需要在云厂商安全组放行 TCP `80`
- 如果之后接入 HTTPS，还需要放行 TCP `443`，并在云服务器或负载均衡层配置证书
- 域名需要添加 `A` 记录，解析到云服务器公网 IP

上线时建议设置：

```env
BASE_URL=http://你的域名
HTTP_PORT=80
BACKEND_ORIGIN=http://api:8000
```

当前 `ops/nginx/default.conf` 使用 `server_name _;`，可以直接用任意绑定到服务器的域名访问。如果需要多站点或 HTTPS，需要另行调整 Nginx 配置。

## Docker 与 Docker Compose 要求

服务器需要安装：

- Docker Engine
- Docker Compose v2，也就是 `docker compose` 命令
- Git

检查命令：

```bash
docker --version
docker compose version
git --version
```

建议使用 Docker 官方源安装 Docker，不建议使用过旧的系统仓库版本。

## 代码和 skill 依赖目录放置

Compose 会把项目父目录挂载到容器内的 `/workspace`：

```yaml
volumes:
  - ..:/workspace:ro
```

因此服务器上的目录结构应保持为一个完整 workspace，而不是只上传 `vehicle-koubei-web-demo` 目录。推荐放置方式：

```text
/opt/codexwork/
  vehicle-koubei-web-demo/
  data/
    repos/
      vehicle-id-finder/
      auto-koubei-collector/
      dcd-koubei-collector/
      koubei-postprocess/
      koubei-keyword-summary-skill/
  koubei-wordcloud/
```

运行目录是：

```bash
cd /opt/codexwork/vehicle-koubei-web-demo
```

容器内会通过 `WORKSPACE_ROOT=/workspace` 访问这些依赖目录：

- `/workspace/data/repos/vehicle-id-finder`
- `/workspace/data/repos/auto-koubei-collector`
- `/workspace/data/repos/dcd-koubei-collector`
- `/workspace/data/repos/koubei-postprocess`
- `/workspace/data/repos/koubei-keyword-summary-skill`
- `/workspace/koubei-wordcloud`

如果依赖目录缺失，worker 即使能启动，也无法完成对应采集、后处理、摘要或词云任务。

## `.env` 必填项

首次部署时复制环境变量模板：

```bash
cp .env.example .env
```

然后编辑 `.env`。上线前至少需要确认以下项目。

### 周口令

项目通过周口令哈希做访问门禁：

```env
PASS_PHRASE_HASH=sha256:...
PASS_PHRASE_VERSION=2026-W17
SESSION_SECRET=请改成高强度随机字符串
```

要求：

- 不要使用 `.env.example` 中的默认周口令
- `PASS_PHRASE_HASH` 使用 `sha256:<hex>` 格式
- `PASS_PHRASE_VERSION` 建议按周更新，例如 `2026-W17`
- `SESSION_SECRET` 必须改成随机值，不能使用 `change-me`

可用以下命令在服务器上生成 SHA-256 哈希：

```bash
printf '%s' '你的周口令' | sha256sum
```

然后写入：

```env
PASS_PHRASE_HASH=sha256:上一步输出的64位hex
```

### Tavily

Tavily 用于联网检索能力：

```env
TAVILY_API_KEY=你的Tavily密钥
```

如果不配置，依赖 Tavily 的检索、补充资料或 grounded QA 能力会失败或降级。

### 数据库

Postgres 由 Compose 内置启动：

```env
POSTGRES_DB=koubei
POSTGRES_USER=koubei
POSTGRES_PASSWORD=请改成强密码
DATABASE_URL=postgresql+psycopg://koubei:请改成强密码@postgres:5432/koubei
```

要求：

- `POSTGRES_PASSWORD` 和 `DATABASE_URL` 中的密码必须一致
- 首次启动后，Postgres 数据会保存在 Docker volume `postgres_data`
- 如果已经启动过并创建了 volume，之后只改 `.env` 里的数据库密码不会自动修改 volume 内已有数据库用户密码

Redis 默认配置通常无需修改：

```env
REDIS_URL=redis://redis:6379/0
```

### LLM

AI 一页报告和问答依赖 LLM 配置：

```env
LLM_PROVIDER=你的供应商标识
LLM_API_KEY=你的LLM密钥
LLM_BASE_URL=你的LLM接口地址
LLM_MODEL_REPORT=用于报告生成的模型
LLM_MODEL_QA=用于问答的模型
```

上线前请确认所选模型可访问、余额充足，并且服务器网络能访问对应 API。

### 其他关键项

```env
APP_ENV=production
BASE_URL=http://你的域名
BACKEND_ORIGIN=http://api:8000
HTTP_PORT=80
ARTIFACT_ROOT=/srv/koubei/jobs
CORPUS_ROOT=/srv/koubei/corpus
CORPUS_HOST_PATH=./storage/corpus
WORKSPACE_ROOT=/workspace
WORKER_QUEUE_NAME=vehicle-koubei
TEMPORAL_ADDRESS=temporal:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=vehicle-koubei-temporal
AUTOHOME_COLLECTOR_SERVICE_URL=http://autohome-collector:8100
DCD_COLLECTOR_SERVICE_URL=http://dongchedi-collector:8100
JOB_ARTIFACT_CLEANUP_ENABLED=false
COMPARISON_MODEL_CONCURRENCY=2
OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2
OPENCLAW_AGENT_LEASE_SECONDS=2400
OPENCLAW_AGENT_POOL_WAIT_SECONDS=1800
```

上线前需要在 OpenClaw 中从现有 `autohome`、`dongchedi` agent 复制出 `autohome-1`、`autohome-2`、`dongchedi-1`、`dongchedi-2`，并同步 auth、模型和 skill 配置。

Temporal 部署由环境变量控制。单机 sandbox 可以使用 Compose 内置 `temporal` 服务；生产环境如果接入外部 Temporal 集群，只需要把 `TEMPORAL_ADDRESS`、`TEMPORAL_NAMESPACE` 和 `TEMPORAL_TASK_QUEUE` 指向目标集群，不需要改业务代码。

新任务中心依赖 `autohome-collector` 和 `dongchedi-collector` 两个 collector service。它们是采集执行入口；生产启用新任务前，必须确认 API 已真正启动 Temporal workflow，且 Temporal activities 已通过 `AUTOHOME_COLLECTOR_SERVICE_URL`、`DCD_COLLECTOR_SERVICE_URL` 调用 collector HTTP API，不应直接在 API 容器里跑采集。

## 首次启动步骤

1. 准备服务器并安装 Docker、Docker Compose v2、Git。
2. 将完整 workspace 放到服务器，例如 `/opt/codexwork`。
3. 确认 `vehicle-koubei-web-demo` 与外部依赖目录位于同一个 workspace 下。
4. 进入项目目录：

```bash
cd /opt/codexwork/vehicle-koubei-web-demo
```

5. 创建并编辑 `.env`：

```bash
cp .env.example .env
nano .env
```

6. 构建并启动：

```bash
docker compose up -d --build --scale temporal-worker=2
```

7. 查看容器状态：

```bash
docker compose ps
```

`temporal-worker` 应显示 2 个副本；如果只看到 1 个 `temporal-worker`，使用：

```bash
docker compose up -d --scale temporal-worker=2
```

8. 查看启动日志：

```bash
docker compose logs -f nginx web api worker
```

9. 浏览器访问：

```text
http://你的域名/
```

## 健康检查命令

检查 Compose 服务状态：

```bash
docker compose ps
```

检查 Nginx 对外健康接口：

```bash
curl -i http://127.0.0.1/healthz
curl -i http://你的域名/healthz
```

检查 API 容器健康接口：

```bash
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
```

检查 Redis：

```bash
docker compose exec redis redis-cli ping
```

检查 Postgres：

```bash
docker compose exec postgres sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

查看 worker 日志：

```bash
docker compose logs --tail=200 worker
```

检查 Temporal 和 collector：

```bash
docker compose ps temporal temporal-worker autohome-collector dongchedi-collector
docker compose logs --tail=100 temporal-worker autohome-collector dongchedi-collector
```

查看全部服务最近日志：

```bash
docker compose logs --tail=200
```

## Sandbox 验证与生产切换

生产切换前必须先用 sandbox 环境跑通过模拟负载。当前仓库内的 fake integration 测试覆盖 workflow 状态机、共享 run、拥挤调度和降级后升级；正式切换前还需要用实际 collector service 路径再跑一次。至少覆盖：

- 1 个单车型增量任务。
- 1 个 2 车型以上对比任务。
- 4 用户模拟负载：单车型 A、A/B/C 对比、另一个单车型 A、D/E/F/G/H 对比。
- 相同车型 A 的平台采集 run 被共享。
- 拥挤模式下每个任务同时只占用一条采集车道。
- 注入失败时先发布降级结果，重试成功后升级为完整结果。

Compose sandbox 示例：

```bash
docker compose -p vehicle-koubei-sandbox --env-file .sandbox/vehicle-koubei/.env up -d --build --scale temporal-worker=2
curl -fsS http://localhost:18080/healthz
docker compose -p vehicle-koubei-sandbox --env-file .sandbox/vehicle-koubei/.env ps
```

生产 cutover 只把新的查询入口切到任务中心；旧 `jobs` 路由和历史结果页面先保留，只是不再作为新任务入口。确认新任务中心稳定后，再单独规划旧接口清理。

## 历史 raw Excel backfill

服务器上如果还保留少量旧的汽车之家或懂车帝 raw Excel，可以一次性导入长期语料库。缺失的平台不要伪造数据，脚本会跳过并在 summary JSON 里标记 `skipped`。

```bash
docker compose exec worker sh -lc 'python scripts/backfill_raw_excels.py \
  --database-url "$DATABASE_URL" \
  --corpus-root "$CORPUS_ROOT" \
  --model-name "风云T11" \
  --query "风云T11" \
  --autohome-series-id 8089 \
  --autohome-xlsx /path/autohome.xlsx \
  --dcd-series-id 25398 \
  --dcd-xlsx /path/dcd.xlsx'
```

导入后检查对应车型目录：

```bash
docker compose exec worker sh -lc 'find "$CORPUS_ROOT" -maxdepth 3 -type f | sort | head -100'
```

## 常见问题

### 80 端口无法访问

排查项：

- 云厂商安全组是否放行 TCP `80`
- 服务器防火墙是否放行 `80`
- `.env` 中 `HTTP_PORT` 是否为 `80`
- 是否已有宿主机 Nginx、Apache 或其他进程占用 `80`

检查命令：

```bash
sudo ss -lntp | grep ':80'
docker compose ps nginx
docker compose logs --tail=100 nginx
```

如果宿主机已有服务占用 `80`，可以停止宿主机服务，或把 `.env` 改成其他端口：

```env
HTTP_PORT=8080
```

然后重新启动：

```bash
docker compose up -d
```

### 数据库密码或 volume 问题

首次启动后，数据库数据会保存在 Docker volume `postgres_data`。如果已经初始化过数据库，再修改：

- `POSTGRES_PASSWORD`
- `POSTGRES_USER`
- `POSTGRES_DB`
- `DATABASE_URL`

可能导致 API 连接失败，因为旧 volume 内的数据库账号密码没有随 `.env` 自动改变。

处理方式：

- 如果是生产数据，不要删除 volume，应该进入数据库后执行密码变更并同步 `.env`
- 如果只是测试环境，可以先确认不需要数据，再删除 volume 后重新初始化

查看 volume：

```bash
docker volume ls | grep postgres_data
```

### Tavily 未配置

如果 `TAVILY_API_KEY` 为空，依赖联网检索的能力会失败或降级。处理方式是申请 Tavily API Key，写入 `.env`，然后重启 API 和 worker：

```bash
docker compose up -d api worker
```

### `agent-browser` 缺失

当前 worker 可以在 Docker 中启动，但汽车之家采集仍依赖外部 `agent-browser` CLI。当前 Compose 配置只会把缺失情况记录为 warning，不会伪装成已经解决。

影响：

- worker 进程可启动
- 涉及汽车之家真实采集的任务会失败
- 需要提供可用的 `agent-browser` CLI，或提供等价的采集运行时后，才能完成真实汽车之家采集

本文不提供虚假的 `agent-browser` 安装方案。上线前应由采集运行时负责人确认该 CLI 或替代运行时如何交付到 worker 可访问的环境中。

### 汽车之家或懂车帝访问失败

可能原因：

- 目标站点限制云服务器 IP、地区、频率或访问行为
- 服务器网络无法访问目标站点
- 采集依赖目录缺失或版本不匹配
- 汽车之家采集缺少 `agent-browser` CLI 或替代运行时
- 目标站点页面结构变化，导致现有采集器失效

建议排查：

```bash
docker compose logs --tail=200 worker
docker compose exec worker sh -lc 'ls -la /workspace/data/repos /workspace/koubei-wordcloud'
```

如果是目标站访问限制或页面结构变化，需要由采集器维护方处理，不应仅通过 Web Demo 层规避。

## 上线前检查清单

- 服务器配置满足推荐规格，磁盘空间足够保存采集结果。
- 域名已解析到服务器公网 IP。
- 云厂商安全组已放行 TCP `80`，如接入 HTTPS 也已放行 TCP `443`。
- Docker Engine、Docker Compose v2、Git 已安装。
- 完整 workspace 已放置到服务器，且 `vehicle-koubei-web-demo` 与外部依赖目录保持相对位置。
- `.env` 已从 `.env.example` 复制并完成修改。
- `APP_ENV=production`。
- `BASE_URL` 已改成线上域名。
- `PASS_PHRASE_HASH` 已替换默认值。
- `PASS_PHRASE_VERSION` 已设置为当前周版本。
- `SESSION_SECRET` 已替换为高强度随机字符串。
- `POSTGRES_PASSWORD` 已替换默认值，且与 `DATABASE_URL` 一致。
- `TAVILY_API_KEY` 已配置。
- LLM provider、API key、base URL 和模型名已配置并验证可用。
- 已确认是否需要真实汽车之家采集；如需要，已提供 `agent-browser` CLI 或替代采集运行时。
- OpenClaw 已准备 `autohome-1/2`、`dongchedi-1/2` 四个采集 agent。
- `docker compose up -d --build --scale temporal-worker=2` 启动成功。
- `docker compose ps` 中核心服务为 running 或 healthy。
- 两个 temporal-worker 副本均在运行。
- `curl http://你的域名/healthz` 返回 `ok`。
- Web 页面可打开，并能通过周口令门禁。
- 已制定 Postgres volume、Redis volume、job artifacts 和 corpus 目录的备份策略。
