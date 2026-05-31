# allWorkflow-20260531-2.md 逻辑审计与优化点（v2.1）

生成时间：2026-05-31  
审计对象：`allWorkflow-20260531-2.md`  
输出目的：检查 v2 文档中的逻辑冲突、逻辑漏洞和可优化点，并作为 `allWorkflow-20260531-3.md` 的整理依据。

## 1. 总体结论

v2 文档已经明确“任务实施、编排方式、多车型对比、增量语料、GET 结果接口、生命周期和访问控制全部采用新流程”，这个方向是正确的。

但文档前半部分仍保留了一些旧流程表达，导致读者会同时看到“新流程是唯一目标”和“旧入口、旧表、旧页面仍需保留”的两套口径。除此之外，新流程里还有几个没有完全闭环的点：评论预览、时间范围报告、QA、访问控制、生命周期、artifact 发布、降级阈值和多车型失败策略。

v3 文档应当做两件事：

1. 删除旧流程描述，只保留新流程的完整目标态。
2. 把原本依赖旧流程的能力全部补成 task-native 能力。

## 2. 逻辑冲突清单

| 编号 | 位置 | 冲突 | 风险 | 优化 |
|---|---|---|---|---|
| C1 | 来源文档 | 来源中继续列 GitHub 旧仓库 main 分支 | v3 容易被理解为旧流程融合文档 | v3 只声明基于 v2.1 优化结论和当前新流程目标 |
| C2 | 信息架构 | 仍写“兼容路由保留”：`/passphrase`、`/vehicle`、`/candidates`、`/progress`、`/result` | 与“删除旧流程”裁决冲突 | v3 信息架构只保留工作台、任务、新建、详情、结果、评论、时间报告等新路由 |
| C3 | 服务职责 | Worker 标为 RQ 兼容能力，Redis 标为队列兼容 | 与新编排以 Temporal 为主的口径不一致 | v3 中 Worker 不再作为目标主执行器；Redis 只作为缓存或运行支持，不作为任务事实源 |
| C4 | 数据库对象 | 表清单仍列 `job_*` 和 `comparison_*` | 与新事实源原则冲突 | v3 只列 `tasks`、`task_vehicles`、`collection_runs`、`task_artifacts`、`koubei_raw_comments` 等新对象 |
| C5 | 事实源原则 | 一边说新任务中心优先，一边说旧表保留兼容 | 读者无法判断新任务写哪套表 | v3 明确只有 task-center 是新任务事实源 |
| C6 | 前端实施顺序 | 第 9 步仍写“保留旧五步路由兼容” | 与删除旧流程冲突 | v3 删除该步骤 |
| C7 | 第 12 节 | 第 12 节说旧流程删除，但前面旧内容没有全部删干净 | 文档自相矛盾 | v3 直接重写完整新流程，不再保留旧流程删除说明 |
| C8 | Artifact 路径 | 当前路径和描述中仍有 `jobs` 语义 | 容易把 task artifact 与旧 job artifact 混在一起 | v3 使用逻辑名 `ARTIFACT_ROOT/{task_id}`，不以旧语义解释 |
| C9 | GET 结果 | 已说 GET 只读，但前文没有定义显式生成/修复 action | GET 只读后缺少补偿入口 | v3 增加 workflow 内生成、显式 action、失败重试边界 |
| C10 | 访问控制 | 新工作台策略没有角色和权限矩阵 | 生产环境无法落地 | v3 增加 viewer/operator/admin 权限划分 |

## 3. 逻辑漏洞清单

| 编号 | 漏洞 | 影响 | 优化 |
|---|---|---|---|
| G1 | 评论预览没有新接口归属 | 旧流程删除后，结果页评论样本没有来源 | 增加 `/api/tasks/{task_id}/comments/summary` 和 `/api/tasks/{task_id}/comments`，从 `koubei_raw_comments` 读取 |
| G2 | 时间范围报告没有新编排 | 旧时间报告删除后，时间版一页纸无法生成 | 增加 task-native `TimeRangeReportWorkflow` 或 activity，结果写 `task_artifacts` |
| G3 | QA 没有新索引生成时机 | GET 不再补建 chunks 后，QA 可能不可用 | 在主 workflow 发布前生成 `qa_chunks`；缺失时返回明确不可用状态 |
| G4 | 多车型失败策略不完整 | 一个车型失败时是否继续对比不明确 | 定义最少 2 个可用车型即可生成降级对比，并记录 excluded vehicles |
| G5 | 增量降级阈值不明确 | 有历史语料就降级可能质量不稳定 | 定义最小样本量、平台完整度、时间范围和 source_status |
| G6 | 生命周期缺少具体分层 | 清理策略可能误删长期语料或有效产物 | 明确 corpus 长期保留、task artifact 按策略保留、collector 临时目录单独清理 |
| G7 | 取消/重试幂等性不完整 | 多用户操作可能重复取消、重复重试或重复创建 run | 所有 action 要求幂等 key 或状态机 guard |
| G8 | 车道排队缺少公平策略 | 多用户或多车型任务可能占满平台 agent | 增加全局并发、单用户并发、平台 lane 上限和 FIFO/优先级规则 |
| G9 | 结果发布缺少原子性 | 部分 artifact 写好但 DB 未发布会造成页面不一致 | 采用 staging -> validate -> publish 的 artifact 发布顺序 |
| G10 | 观测和告警不足 | rate limit、缺产物、schema 变化难以及时定位 | 增加 task_events、collector_events、failure_category、运行巡检查询 |

## 4. 优化决策

### 4.1 删除旧流程表达

v3 不再保留以下内容：

- 旧入口路由。
- 旧任务创建接口。
- 旧对比任务接口。
- 旧 RQ 编排。
- 旧数据表作为工作流事实源。
- 旧 GET 副作用。
- 旧访问门禁作为主链路。
- 旧清理策略作为主生命周期。

### 4.2 保留但暂不重建进度模型

用户已明确进度暂不修改，因此 v3 只做边界说明：

- 页面可以继续展示当前可读的任务状态、collector run 状态和已有进度字段。
- 不在 v3 中定义新的百分比权重。
- 新进度模型后续单独设计，覆盖单车型、双平台、多车型、队列、车道、ETA、降级和重试。

### 4.3 将旧能力迁移为新 task-native 能力

| 能力 | v3 归属 |
|---|---|
| 评论预览 | `koubei_raw_comments` + task comments API |
| 时间范围一页纸 | `TimeRangeReportWorkflow` / task activity + `task_artifacts` |
| QA | workflow 生成 `qa_chunks`，`POST /api/tasks/{task_id}/qa` 只读证据回答 |
| 多车型对比 | native comparison task + child single tasks |
| 下载包 | `task_artifacts` 中的 business bundle |
| 降级结果 | workflow 显式发布 `completed_degraded` |
| 结果读取 | task result API 只读 |
| 清理 | task artifact 与 corpus 分层清理 |
| 访问控制 | 工作台统一认证和角色权限 |

## 5. v3 文档优化结构

v3 应按以下结构组织：

1. 目标与边界。
2. 当前服务角色和逻辑职责。
3. 信息架构。
4. 前端工作台设计。
5. 新任务 API。
6. 单车型 workflow。
7. 多车型 comparison workflow。
8. Collector 与 OpenClaw。
9. 增量语料与降级。
10. 评论、时间报告和 QA。
11. Artifact 与结果发布。
12. 数据对象。
13. 生命周期和访问控制。
14. 状态与进度边界。
15. 异常处理和观测。
16. 实施顺序与验证。

## 6. v3 必须满足的检查项

- 不出现旧流程作为目标工作流。
- 不出现旧任务创建和旧对比创建接口。
- 不出现旧表作为新任务事实源。
- 不出现旧路由保留要求。
- 不出现 GET 结果触发生成的设计。
- 不出现旧访问门禁作为主链路。
- 不出现旧清理策略控制新生命周期。
- 保留视觉系统设计，不改变 Flat Design、米色背景、墨绿主色、炭黑/金色辅助色、中高密度和 8px 圆角。
- 进度只写过渡边界，不写新的权重模型。

