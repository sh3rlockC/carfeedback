# Vehicle Koubei Sandbox

本地沙箱用于复现服务器同构服务形态，同时隔离数据库、Redis、artifact 和端口。

## 启动

1. 同步服务器同源密钥到本地 ignored env：

   ```bash
   scripts/sandbox/sync-server-env.sh
   ```

   默认读取 `/opt/codexwork/carFeedback/.env`。如服务器 env 在其他路径，可设置 `SERVER_ENV_SOURCE`；如果需要 SSH，可使用 `SERVER_ENV_SOURCE=user@host:/opt/codexwork/carFeedback/.env`。脚本只复制允许列表字段：`TAVILY_API_KEY`、`LLM_PROVIDER`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_BATCH`、`LLM_MODEL_REPORT`、`LLM_MODEL_QA`。

2. 启动沙箱：

   ```bash
   scripts/sandbox/up.sh
   ```

3. 访问 `http://localhost:18080`。

## 隔离边界

- Compose project 固定为 `vehicle-koubei-sandbox`。
- 入口端口固定为 `18080`。
- Postgres volume 为 `vehicle-koubei-sandbox-postgres`。
- Redis volume 为 `vehicle-koubei-sandbox-redis`。
- job artifact 在 `.sandbox/vehicle-koubei/storage/jobs`。
- `.sandbox/` 已加入 `.gitignore`，其中的 env、日志、token、数据库和 artifact 都不会进入仓库。

## OpenClaw Token

真实采集回归需要 gateway token。可通过以下方式同步到本地沙箱 secret：

```bash
SANDBOX_OPENCLAW_TOKEN_SOURCE=/path/to/openclaw_gateway_token scripts/sandbox/sync-server-env.sh
```

纯 API、worker 或前端测试不需要 token。
