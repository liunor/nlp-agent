# T07 课堂并发、排队、恢复与额度结算修复实施规格

> 状态：可执行
>
> 适用仓库：`Pro_NLP`
>
> 目标读者：负责实现、压测、故障注入和交付的开发 Agent
>
> 最后核对日期：2026-10-17

## 0. 实施与验收状态（更新于 2026-10-17）

本规格是目标，不表示所有阶段已经交付。当前独立实施分支为
`codex/t07-reliability-fixes`，产品基点为 `8e83a7296a534504f6f5ef4320046d734412142b`。
代码已覆盖 MySQL 权威容量计数、额度 Reservation/Usage Ledger、排队 UI、Worker
heartbeat 指标，以及同步/异步连接池占用与 acquire 延迟观测；具体验证边界见
[复审修复验证记录](t07-review-fix-verification.md)。

仍未完成：detached background resume 的独立持久任务生命周期、工具副作用的持久
幂等 fence、statement/lock/deadlock 观测与告警接入，以及 MySQL 迁移/并发运行验证、
旧版本 50/30 压测与 1×1 → 1×4 → 2×4 故障验收。因此 T07 尚不能标记完成或部署就绪。

## 1. 执行指令

本文件是 T07 的实现与验收基线，不是方向性建议。执行 Agent 必须先建立可重复的负载测试，再修改并发实现；不得只凭单元测试通过就宣称解决课堂并发问题。

开始前必须：

1. 阅读根目录 `CONTEXT.md`、`docs/redis-worker-deployment.md`、`docs/observability.md`、`docs/specs/model-usage-quota-interface-handoff.md`、`docs/specs/model-usage-quota-handoff-implementation.md` 和本文件。原草案引用的 `docs/backend-monitoring-metrics.md` 不在冻结产品基点内，不作为干净工作区的必需文件。
2. 执行 `git status --short`，保护所有既有修改。不得还原、覆盖或格式化无关文件。
3. 确认 T06 已完成，或者由负载测试夹具直接预置 50 个可登录的 student 账号和一个班级；T07 压测不得依赖人工注册。
4. 所有压测只能运行在隔离的 test/staging 环境。脚本必须拒绝连接生产域名，且不得提供绕过参数。
5. 大规模压测默认使用确定性的假模型 Provider，不消耗真实模型额度。真实 Provider 只做最多 5 个任务的冒烟验证。
6. 先保存未修复版本的基线报告，再实施修复；修复后使用完全相同的负载参数复测并对比。
7. 数据库变化只能通过新的 Alembic migration 完成，不得修改历史 migration。

## 2. 已确定的容量目标

以下数字是本轮 T07 的最低验收线，不留给执行 Agent重新选择：

### 2.1 课堂模型

- 一个班级 50 名已登录学生。
- 50 个同时保持的 WebSocket 连接。
- 其中 30 人在 10 秒内提交一个课堂 Turn。
- 同一学生同一会话最多一个 active Turn；不同学生之间允许并行。
- 假模型每个 Turn 固定执行 20 秒，用于制造稳定且可重复的排队。
- 验收部署为 2 个 `nova-worker` 副本，每个副本 4 个执行槽，总执行并发为 8。

### 2.2 服务等级目标

| 指标 | 最低目标 |
|---|---:|
| 50 用户并发登录成功率 | 100% |
| 正确凭据登录错误返回 401 | 0 |
| Turn 提交 HTTP 202 p95 | ≤ 2 秒 |
| Turn 提交 HTTP 202 p99 | ≤ 5 秒 |
| 客户端看到 queued/accepted 状态 | ≤ 2 秒 |
| 30 Turn 假模型场景排队等待 p95 | ≤ 120 秒 |
| Turn 丢失或重复完成 | 0 |
| 假模型场景最终成功率 | 100%（主动取消除外） |
| 排队中取消生效 p95 | ≤ 2 秒 |
| 运行中取消生效 p95 | ≤ 5 秒 |
| Worker 强杀后的 Turn 恢复 | ≤ lease + 15 秒 |
| DB pool 获取等待 p95 | ≤ 200 ms |
| DB pool 获取等待 p99 | ≤ 1 秒 |
| 目标负载下 DB pool timeout/deadlock | 0 |
| 额度重复扣减或泄漏 reservation | 0 |
| 非鉴权故障被映射为 HTTP 401 | 0 |

如果真实 Provider 的限流使完成时间超过目标，必须单独报告 Provider 限制；HTTP 接单、排队、取消、会话稳定性和额度一致性仍必须达到上述目标。

## 3. 已确认的当前风险

执行 Agent 应先用基线测试验证并记录这些事实：

1. `compose.yaml` 默认只有一个 `nova-worker` 服务实例。
2. `RedisWorkerRuntime.run_once()` 每次 `XREADGROUP count=1`，并在读取下一条消息前等待当前 Turn 完成，实际是单 Turn 串行执行。
3. 远程执行路径在 `create_turn()` 中写入 `turn.dispatch` outbox，同时 `gateway.core` 又直接调用 Redis dispatcher；同一 Turn 可能产生重复 Redis 消息，虽然 fencing 可阻止重复执行，但会放大队列和 Redis 压力。
4. 进程内 `_session_turn_locks` 只能保护单个 Web 进程，不能保护多 Web 副本；会话级 active Turn 约束必须落到 MySQL 事务。
5. MySQL pool 只有 `pool_size` 和 `max_overflow`，没有显式 `pool_timeout`，也没有实际 pool wait/timeout 指标。
6. MySQL 已有 Turn claim generation、lease heartbeat、`FOR UPDATE` 和 tool operation idempotency 基础，但尚未完成多 Worker 课堂级故障注入验收。
7. 模型 usage contract 已有 `reservation_id` 字段，但 `TurnTask` 和 Redis codec 没有传播该字段，数据库也没有完整的模型额度 Reservation/Usage Ledger。
8. 当前只有 storage quota reservation；它不能替代模型 Token/计费额度预留。
9. SMS Provider 已通过 `asyncio.to_thread()` 避免直接阻塞事件循环，但 SMS 请求仍可能长时间占用请求和数据库依赖，需要验证慢 Provider 不会拖垮登录与注册。
10. 现有测试主要覆盖组件正确性，没有一个完整的“50 登录 + 30 发任务 + 排队 + 取消 + Worker 强杀 + 恢复 + 额度结算”场景。

## 4. 目标架构

```text
Browser
  │  POST /chat/turns
  ▼
Nova Web
  │  鉴权、Admission、额度预留
  │  在同一 MySQL 事务写 Turn + Outbox
  ▼
MySQL authoritative state
  │  Outbox Relay（唯一 Redis 投递入口）
  ▼
Redis Stream
  │  consumer group，按可用执行槽拉取
  ▼
Worker replica × 2
  │  每副本 4 个有界执行槽
  │  MySQL claim_generation + lease fencing
  ▼
Model / Tools
  │  每次真实 Provider Attempt 写 Durable Usage Event
  ▼
Settlement
  └─ 成功、失败、取消、中断都结算已用额度并释放剩余额度
```

核心原则：

- MySQL 是 Turn、Admission、Reservation、Claim 和终态的权威来源。
- Redis 只负责至少一次投递，不是最终状态库，也不是唯一容量计数器。
- Web 请求只负责可靠接单，不等待模型执行。
- Worker 有界并发，不无限创建 task，不超量预取 Redis 消息。
- 所有非认证故障使用 409/429/503 或 Turn 失败事件，绝不返回用户认证 401。

## 5. 阶段 0：建立红色反馈环

### 5.1 新增负载测试工具

新增：

```text
tests/load/t07_classroom_load.py
tests/load/README.md
tests/load/profiles/t07-50-users.json
tests/load/fake_model_provider.py
```

脚本使用现有 `httpx`、`asyncio` 和 WebSocket 客户端能力，不为一次压测引入重量级常驻框架。必须支持：

```powershell
uv run python tests\load\t07_classroom_load.py `
  --base-url http://127.0.0.1:8765 `
  --profile tests\load\profiles\t07-50-users.json `
  --run-kind baseline `
  --baseline-ref 8e83a7296a534504f6f5ef4320046d734412142b `
  --output artifacts\load-tests\t07\baseline.json
```

脚本必须：

1. 验证 `/health/ready` 返回的 deployment env 是 `test` 或 `staging`。
2. 预置或读取 50 个独立账号，禁止所有虚拟用户共用一个 Cookie jar。
3. 为每个请求生成稳定的 idempotency key。
4. 记录每个阶段的开始、202、queued、running、首 token、终态时间。
5. 记录 HTTP status 和 problem `code`，特别统计 401、409、429、503。
6. 检测同一 `turn_id` 多次终态、任务丢失、事件序列缺口和超时。
7. 输出机器可读 JSON；不得只输出终端文字。
8. 失败时返回非零退出码。

### 5.2 必须提供的负载 Profile

- `auth_burst`：50 用户在 10 秒内登录并读取 session。
- `turn_burst`：50 在线，30 用户在 10 秒内提交 Turn。
- `cancel_queued`：30 Turn 中取消 10 个尚未运行的任务。
- `cancel_running`：取消 4 个运行中任务。
- `kill_worker`：执行中强杀一个 Worker，等待 lease 恢复。
- `redis_pause`：短暂停止 Redis，再恢复并验证 outbox 补投。
- `db_delay`：注入 500 ms 数据库延迟，验证 backpressure 和错误分类。
- `slow_sms`：SMS Provider 固定延迟 5 秒，同时执行登录和 Turn 提交。
- `quota_exhaustion`：使用极小测试额度触发 admission 拒绝。
- `provider_errors`：依次模拟 Provider 401、429、503、timeout。

### 5.3 基线报告

baseline 模式必须同时验证执行脚本的旧版本 checkout 与目标服务返回的
`baseline_product_commit`；两者均须匹配冻结产品基点。旧版本缺少 health 环境字段时，
只能使用 `tests/load/baseline_entrypoint.py` 的隔离测试适配器，不能把新版产品复制过去。

修复前至少运行 `auth_burst`、`turn_burst` 和 `provider_errors`，将结果保存为：

```text
artifacts/load-tests/t07/baseline.json
artifacts/load-tests/t07/baseline-summary.md
```

报告必须包含机器配置、镜像版本、Git commit、worker 数量、每 worker 并发、DB pool 配置和假模型延迟。未得到红色或不能解释基线时，不得进入扩容实现。

## 6. 阶段 1：修正接单与投递路径

主要文件：

- `gateway/core.py`
- `gateway/mysql_repository.py`
- `gateway/redis_transport.py`
- `server/application/turn_reliability.py`
- `tests/test_gateway_core.py`
- `tests/test_redis_transport.py`
- `tests/test_turn_reliability.py`

### 6.1 Outbox 是远程执行的唯一投递入口

远程 Redis 模式下：

1. `create_turn()` 必须在一个 MySQL 事务内写入 Turn、用户消息、模型额度 Reservation 和 `turn.dispatch` outbox。
2. 事务提交后直接返回 HTTP 202。
3. `gateway.core` 不得再次调用 `RedisTurnDispatcher.submit()` 做第二次 `XADD`。
4. 只有 Outbox Relay 可以将该 Turn 写入 Redis Stream。
5. in-process 开发模式仍可使用 `InProcessTurnDispatcher`，但不得写远程 outbox。

新增测试必须证明：

- 一个成功提交只产生一条待发布 outbox。
- relay 重试可以产生 Redis 的重复消息，但同一 Turn 只执行一次。
- MySQL 提交成功、Redis 暂时不可用时 HTTP 仍返回 202，恢复后 outbox 会补投。
- MySQL 事务失败时既没有 Turn，也没有 outbox 或 Reservation。

### 6.2 跨进程单会话约束

不要只依赖 `_session_turn_locks`。在创建 Turn 的 MySQL 事务中：

1. `SELECT ... FOR UPDATE` 锁定对应 `nlp_conversations` 行。
2. 查询该 conversation 是否已有 `accepted` 或 `running` Turn。
3. 已有时返回 `409 turn_already_active` 和现有 `turn_id`。
4. 没有时才创建新 Turn。

`_session_turn_locks` 可以保留作为同进程快速路径，但数据库锁才是正确性边界。

## 7. 阶段 2：Admission、排队和过载保护

### 7.1 配置

新增并校验：

```text
NLP_AGENT_TURN_QUEUE_CAPACITY=200
NLP_AGENT_TURN_QUEUE_PER_USER=3
NLP_AGENT_WORKER_CONCURRENCY=4
NLP_AGENT_WORKER_HEARTBEAT_INTERVAL_S=5
NLP_AGENT_WORKER_HEARTBEAT_TTL_S=15
NLP_AGENT_DB_POOL_TIMEOUT_S=5
NLP_AGENT_TURN_LEASE_S=60
```

限制：worker concurrency 为 1–16；queue capacity 至少为 1；同一会话仍只允许一个 active Turn。

### 7.2 MySQL 权威容量计数

新增 migration 和模型：

```text
nlp_runtime_capacity
- scope_key VARCHAR(128) PRIMARY KEY     # 第一阶段固定 global:turns
- queued_count BIGINT NOT NULL
- running_count BIGINT NOT NULL
- updated_at DATETIME(6) NOT NULL
```

Admission 在创建 Turn 的同一事务中锁定 `global:turns` 行：

- `queued_count >= capacity`：HTTP `429 queue_capacity_exceeded`，带 `Retry-After: 5`。
- 用户达到 per-user accepted/running 上限：`429 user_turn_limit_reached`。
- 接受 Turn：原子增加 queued。
- Worker claim：原子执行 queued - 1、running + 1。
- completed/failed/cancelled/interrupted：原子减少 running；排队中取消只减 queued。
- 出现负数企图必须告警，不能只用 `GREATEST` 静默掩盖。

启动时和每 60 秒从 `nlp_turns` 权威状态 reconcile；发现漂移要修正并记录指标。

### 7.3 Worker 健康与排队 UI

Worker 每 5 秒写带 TTL 的 Redis heartbeat，包含 worker ID、启动时间、concurrency、active slots 和版本。30 秒内无健康 Worker时，新 Turn 返回 `503 worker_unavailable`；已有 Turn 保留等待恢复。

扩展 Turn 查询响应，提供近似 `ahead`、`entered_at` 和 `waited_ms`。前端显示“排队中，前方约 N 个任务”，排队期间始终允许取消；刷新或 WebSocket 重连后通过 GET Turn 和 event replay 恢复。

## 8. 阶段 3：有界 Worker 并发与多副本

主要文件：`gateway/redis_transport.py`、`server/worker/runtime.py`、`server/worker/fencing.py`、配置、Compose、部署文档和对应测试。

### 8.1 单进程并发

1. 使用有界 semaphore，默认 4 个执行槽。
2. `XREADGROUP count` 不超过当前空闲槽数。
3. 每条消息由独立 `_process_message()` task 处理。
4. 只有终态持久化后才 ACK。
5. shutdown 时停止拉取，等待 grace period；超时取消但不 ACK。
6. 不得预取超过执行槽数的消息。
7. 控制订阅、heartbeat、取消和 active map 支持多个 Turn。
8. 单 Turn 异常不能终止消费循环或取消其他 Turn。

### 8.2 Fencing 与部署

启用第二副本前必须证明：

- `claim_generation` 是所有权 fencing token，旧 generation 的 heartbeat、终态、event 和 tool completion 全被拒绝。
- 所有有副作用工具使用 `turn_id + operation_id` 幂等键。
- Worker A 在 Provider 成功后、终态前死亡时，Worker B 不重复不可逆副作用。
- 重复 Redis message 不触发第二次模型调用。

任何有副作用工具未接持久幂等时，第二副本不得上线。通过后 staging 使用：

```powershell
$env:NLP_AGENT_WORKER_CONCURRENCY='4'
docker compose up -d --scale nova-worker=2
```

本 T07 不扩到 2 个以上副本。

## 9. 阶段 4：数据库连接、锁等待与事务边界

### 9.1 Pool 预算

为 `DatabaseConfig` 增加 `pool_timeout_s` 并传给 `create_async_engine()`。

| 进程 | 副本 | pool_size | max_overflow | 最大连接 |
|---|---:|---:|---:|---:|
| nova-web | 1 | 10 | 10 | 20 |
| nova-worker | 2 | 8 | 4 | 24 |
| nova-monitor | 1 | 3 | 2 | 5 |
| sandbox-manager/其他 | 合计 | 5 | 5 | 10 |
| 预留迁移和运维 | - | - | - | 10 |
| 合计预算 | - | - | - | 69 |

MySQL `max_connections` 至少 100；所有应用理论最大连接总数不得超过其 70%。

### 9.2 事务和错误

- 禁止持有数据库事务、行锁或连接时调用模型、SMS、外部 HTTP、Docker 或文件上传。
- Turn claim/heartbeat 使用短事务，模型执行发生在 claim 提交后。
- outbox 必要时拆成“短事务 claim → Redis publish → 短事务 finalize”，不得在慢 Redis 上长期持锁。
- 连接等待超过 5 秒：`503 database_busy`，`Retry-After: 1`。
- statement/lock wait timeout：`503 database_timeout`。
- deadlock 仅对幂等短事务做最多 2 次 jitter retry。
- 上述错误绝不包装成 `AuthenticationError`。

### 9.3 指标

增加 DB pool checked-out、overflow、pending checkout 数、acquire latency（count/sum/max/固定延迟桶）、timeout，以及 statement latency、deadlock；同时观测 turn queue depth/oldest age/wait、worker slots、recoveries。不得把 user/session/turn/classroom ID 放进全局 metric label。

## 10. 阶段 5：模型额度预留、结算与释放

本节只处理模型使用额度。storage quota 保持现状，不得复用 `nlp_storage_reservations` 冒充模型额度。

### 10.1 数据模型

新增 migration 和模型：

```text
nlp_model_quota_accounts
- user_id PK/FK
- window_started_at / window_ends_at
- token_limit NULLABLE        # NULL 表示不限制，但仍记录 usage
- used_tokens / reserved_tokens / updated_at

nlp_model_reservations
- id UUID PK
- turn_id UNIQUE/FK
- user_id / workspace_id / model_profile
- reserved_tokens / settled_tokens
- status                      # reserved/settled/released/expired
- created_at / finalized_at / expires_at

nlp_model_usage_events
- operation_id UNIQUE
- reservation_id / turn_id / user_id / workspace_id
- provider / provider_model / pricing_key / profile / preset / route
- purpose / worker_id / attempt / fallback_index
- input_tokens / cached_input_tokens / cache_write_input_tokens
- output_tokens / reasoning_output_tokens / total_tokens / source
- provider_response_id / outcome_status / finish_reason / error_kind
- started_at / completed_at
- pricing_version NULLABLE / charge_units NULLABLE
```

字段语义遵守 `docs/specs/model-usage-quota-interface-handoff.md`，不能只保存 `total_tokens`。

### 10.2 Admission

创建 Turn 前：

1. 解析实际 model profile 和 role。
2. 完整 Prompt 在 Worker 组装，接单阶段采用所选 coordinator 模型最大输入上下文加最大输出的保守预算，覆盖历史、系统提示、教学材料与工具 schema。不得只用本次用户文本的正常估算作为整段 Prompt 预算；估算失败不得当作 0。
3. 每一次 Provider attempt（包括 retry、fallback、工具后续调用）必须先提交持久调用 Intent，并按该次模型的上下文上限加输出上限检查/追加预留，提交后才允许调用 Provider。预算不足时不得发起本次模型请求。这一保守策略可能提前拒绝有限额度的短请求；精算优化必须基于完整 Prompt。

调用授权还必须核对当前 Turn 的执行所有权：持有 Turn 行锁，按统一锁顺序取得额度相关锁后，以数据库当前 UTC 时间校验 `running`、`claim_generation`、`claimed_by`、`lease_expires_at`，并校验本地共享 fence；Intent 行锁等待结束后再次校验租约和本地 fence。最后一次数据库时钟查询前记录 monotonic 锚点，将剩余租约换成绑定该 fence 的保守、单次派发期限，查询/COMMIT 耗时只能缩短期限。实际 Provider 派发前再无等待地检查本地 fence 与派发期限，防止 COMMIT 等待跨过租约而 heartbeat 尚未发现失权时继续调用。该门禁只用于新调用，不能阻止旧 generation 已发生费用的迟到上报或重放。这是派发授权点的检查，不是对外部 Provider 宣称 exactly-once。

数据库准备使用独立的默认 5 秒期限（`preparation_timeout_s`），覆盖连接/行锁等待和准备 SQL；超时退出事务并回滚，分类为 `usage_preparation_timeout`，不得计入 Provider 熔断。期限在 COMMIT 前结束，避免主动取消 COMMIT 导致结果不明；COMMIT 等待也不计入 Provider 期限。流式 Provider 的 total/TTFT 从准备成功后开始。Intent 已提交但确定尚未派发（如本地 fence 撤销或派发前超时）时，以 `estimated` 零用量、`provider_not_dispatched` 结束该 Intent，不制造未知费用；若已经开始迭代/派发，则不得假定零成本。
4. 锁定用户 quota account，原子检查 `used + reserved + requested <= limit`。
5. Reservation、Turn、用户消息和 outbox 在同一事务提交。
6. 额度不足返回 `429 quota_admission_denied`，不创建 Turn。

测试环境通过配置设置 token limit；生产 limit 为 `NULL` 时仍创建 Reservation 和 Usage Event，但不做用户额度拒绝。

### 10.3 跨进程传播

扩展 `TurnModel.reservation_id`、`TurnTask.reservation_id`、`TurnTaskCodec` 协议、Worker 恢复、outbox 重放和 `UsageAttributionContext`。

同一 Turn 重放沿用同一个 reservation ID；每次真实 Provider Attempt 使用新的 operation ID。协议升级前先排空旧队列，或完成 v1/v2 双读；缺少 Reservation 的旧任务不得静默按免费任务执行。

### 10.4 Durable Reporter 与结算

实现 MySQL Durable `ModelUsageReporter`：

- operation ID 幂等插入，重复事件不重复扣减；同 ID 内容冲突时告警并拒绝覆盖。
- Provider 成功但 reporter 失败时不得重新调用 Provider。
- retry/fallback 的每次真实调用都计入实际使用。
- `source="none"` 不得视为精确零成本，必须标记待对账。

新增 `nlp_model_usage_intents`，以 operation ID 为主键，保存 reservation/user、调用预算、完整 invocation 身份及 pending/reported/unknown_usage 状态；Reservation 增加 `pending_invocations`。每次 Provider 调用之前，Intent 和预算追加同事务提交。已知 usage 入账时从尚未使用的 Reservation 移出对应金额，避免 `used` 与 `reserved` 双重占用同一笔已知成本。

Provider 返回后的精确 usage 先写入持久 `.data/model-usage-outbox`，文件 fsync 后原子发布，再尝试 MySQL Usage Event 事务；事务成功后才删除文件。Worker 启动、运行中和关闭排空后重放此 outbox，重复 operation ID 不重复扣减。所有生产 Worker 必须挂载并保留共享 `.data` 卷；不能把待补报文件放在临时层。

数据库使用统一的短事务锁顺序：Turn（如涉及）→ `global:turns` 容量锁 → Reservation → Account。对账候选查询仅作提示；逐项释放使用新的锁定读取。余额汇总在独立事务取得同一容量锁，再用 Reservation `FOR UPDATE` 当前读汇总，最后锁 Account 修正，禁止用旧 REPEATABLE READ 快照覆盖当前余额。

终态规则：

- completed/failed/cancelled：结算已发生 usage；仅在 pending Intent 已处理且无未知用量后释放剩余 Reservation。Reporter 事务失败、磁盘不可写或 Provider 中途消失时，持久 Intent 保留对应额度并暴露待对账指标，不能因 Turn 终态释放。只有 Intent、没有精确返回事实时，须人工/Provider 对账，不能伪造零成本自动释放；生产告警接入仍按未完成清单推进。
- 取消后迟到的已知 usage 必须同时更新 `used_tokens` 与 `settled_tokens`；仍持有 pending 额度的终态 Turn，在最后一笔报告完成时结算释放。
- interrupted 且将恢复：保留 Reservation；最终终态才释放。
- Reservation 过期且 Turn 已终态或不存在：仅在无 pending/unknown usage 时允许 reconciler 释放并告警。

Worker 退出时必须先排空/取消执行和相关计量，再重放待补报事实，最后卸载 Reporter；调用关闭协程的任务被取消不能中断这一排空顺序。Revision `20261017_54_t07` 上线前先排空旧 Worker，避免无 Intent 的旧调用混跑；降级前必须处理 pending/unknown_usage Intent，迁移不得删除待补报事实。

### 10.5 错误区分

- Pro_NLP 用户额度不足：`quota_admission_denied`。
- Provider 账户欠费：Turn failed `upstream_provider_quota_exhausted`。
- Provider 401/403：Turn failed `upstream_auth_failed`。
- Provider 429：`upstream_rate_limited`，按 retry policy。

Provider 错误不得清除浏览器 Cookie，不得让 `/auth/session` 变成 401。

## 11. 阶段 6：SMS、登录与认证隔离

主要文件：`server/web/app.py`、`server/user/tencent_sms.py`、`server/auth/code_store.py` 和注册 HTTP 测试。

1. CAPTCHA 消费、rate-limit reservation 和验证码持久化使用短事务。
2. 调用同步 Tencent SDK 时不得持有数据库连接或事务。
3. Provider 使用独立、有限并发的 executor/semaphore，默认最大 8。
4. Provider timeout 默认 5 秒；超时返回 `502 sms_gateway_timeout`。
5. SMS 并发已满返回 `429 sms_gateway_busy`，不排无限内存队列。
6. Provider 失败时验证码不可使用；发送尝试仍计入防滥用限制。
7. `slow_sms` 场景中，正常登录和 `/auth/session` p95 相比无 SMS 基线增幅不超过 20%。

## 12. 统一错误分类

只有 Cookie 缺失、无效、过期或 T06 定义的权限版本变化可以返回 HTTP 401。

| 故障 | 对外结果 |
|---|---|
| 队列满 | 429 `queue_capacity_exceeded` |
| 用户 Turn 数超限 | 429 `user_turn_limit_reached` |
| 用户模型额度不足 | 429 `quota_admission_denied` |
| 无健康 Worker | 503 `worker_unavailable` |
| DB pool 耗尽 | 503 `database_busy` |
| DB/锁超时 | 503 `database_timeout` |
| Redis 中断但 MySQL 接单成功 | 202，outbox 等待补投 |
| Provider 401/403 | Turn failed `upstream_auth_failed` |
| Provider 429 | Turn retrying/failed `upstream_rate_limited` |
| Provider 5xx | Turn retrying/failed `upstream_overloaded` |
| 用户认证失败 | 401 认证类 problem code |

增加端到端守卫测试：每个非鉴权故障注入后，调用 `/api/v1/auth/session` 必须仍为 200。

## 13. 测试清单

### 13.1 单元与集成测试

- `tests/test_redis_transport.py`：4 槽并发、不超量预取、独立 ACK、异常隔离、优雅关闭。
- `tests/test_worker_fencing.py`：两 Worker 竞争、旧 generation 拒绝、心跳丢失、取消。
- `tests/test_turn_reliability.py`：outbox 单一投递、恢复重放、容量计数 reconcile。
- `tests/test_gateway_core.py`：远程模式不重复 XADD、跨进程 active Turn 约束。
- `tests/test_model_usage_contracts.py` 和 `tests/test_model_usage_reporting.py`：Reservation 传播、Attempt 幂等、终态释放。
- 新增 `tests/test_model_quota_reservations.py`：并发 Admission 不超卖、取消释放、失败结算、reconcile。
- `tests/api_http/chat/test_chat_worker_async_core.py`：202、排队、取消、恢复、错误码。
- `tests/api_http/auth/test_registration_core.py`：slow SMS 不阻塞登录。

### 13.2 故障注入

必须自动化：

1. Worker 在模型调用前死亡。
2. Worker 在模型成功后、终态提交前死亡。
3. Redis 停止 10 秒后恢复。
4. MySQL 查询延迟和 pool timeout。
5. Provider 401、429、503、timeout、半流中断。
6. Durable Reporter 在 Provider 成功后失败。
7. 取消与 Worker claim 同时发生。
8. Reservation settle 与 recover 同时发生。

每个场景断言 Turn 终态、模型调用次数、工具副作用次数、usage event 数和 reservation 余额。

### 13.3 测试命令

```powershell
uv run pytest `
  tests\test_redis_transport.py `
  tests\test_worker_fencing.py `
  tests\test_turn_reliability.py `
  tests\test_gateway_core.py `
  tests\test_model_usage_contracts.py `
  tests\test_model_usage_providers.py `
  tests\test_model_usage_reporting.py `
  tests\test_model_quota_reservations.py -q
```

真实 HTTP/Redis/MySQL：

```powershell
$env:RUN_API_HTTP='1'
uv run pytest `
  tests\api_http\auth\test_registration_core.py `
  tests\api_http\chat\test_chat_worker_async_core.py `
  tests\api_http\websocket\test_websocket_async_core.py -q
```

前端与完整回归：

```powershell
Set-Location webui
npm test -- --run
npm run typecheck
npm run build
Set-Location ..
uv run pytest -q
```

Docker 不可用时，不得把真实 HTTP 或故障注入测试写成通过；必须报告阻塞。

## 14. 实施顺序与建议提交

1. `test(t07): add reproducible classroom load harness`
2. `fix(gateway): make outbox the single remote dispatch path`
3. `feat(gateway): add durable turn admission and queue state`
4. `feat(worker): execute redis turns with bounded concurrency`
5. `fix(worker): harden multi-replica fencing and idempotency`
6. `feat(database): add pool timeout metrics and connection budgets`
7. `feat(quota): reserve and settle model usage per turn`
8. `fix(auth): isolate slow sms and non-auth failures`
9. `feat(web): expose queued state and cancellation UX`
10. `test(t07): pass classroom load and fault-injection gates`

每阶段保持已有测试通过。不得先改 Compose 扩容、后补 fencing；扩容是最后结果，不是第一步。

## 15. 发布步骤

### 15.1 发布前 Gate

- migration 在生产数据副本上成功。
- 现有 accepted/running 队列已排空，或 codec 已完成兼容验证。
- 所有有副作用工具通过幂等审计。
- staging 的 50/30 完整负载测试通过。
- worker kill、Redis pause、quota exhaustion、provider errors 全部通过。
- MySQL 最大连接预算已核对。

### 15.2 灰度

1. 先部署 Web 和数据库结构，但保持 1 worker × 1 slot。
2. 开启 1 worker × 4 slots，运行 10 用户小流量。
3. 开启 2 workers × 4 slots，运行 30 用户负载。
4. 通过后运行完整 50/30 验收。
5. 观察至少一个完整课堂时段后再标记完成。

出现重复模型调用、副作用、Reservation 泄漏、DB pool timeout 或非鉴权 401，立即回退到前一个容量档位。

### 15.3 回退

- 首选把 worker 副本降到 1、concurrency 降到 1；不要回退数据库 migration。
- 新 Turn Admission 可临时降低 queue capacity，不能关闭容量保护。
- 保留新版 codec 读取能力，避免回退后无法处理已写入任务。
- quota reporter 故障时停止新的模型 Admission，不得放行免费调用。
- 不得关闭 claim fencing、取消 Reservation 或扩大成无限队列。

## 16. 最终验收报告

执行 Agent 必须提交：

```text
artifacts/load-tests/t07/baseline.json
artifacts/load-tests/t07/final.json
artifacts/load-tests/t07/comparison.md
```

`comparison.md` 至少包含版本、机器资源、服务配置、HTTP 和 queue latency p50/p95/p99、401/409/429/503 分类、DB pool wait/timeout/deadlock、Worker slots/恢复/重复投递、Usage/Reservation 对账、取消与故障注入结果及剩余限制。

## 17. 完成定义

只有同时满足以下条件，T07 才可标记完成：

- [ ] 基线和修复后报告均可重复生成。
- [ ] 50 用户登录和 30 Turn burst 达到第 2 节指标。
- [ ] 远程 Turn 只有 outbox 一个投递入口。
- [ ] 单会话 active Turn 约束在多 Web 进程下仍成立。
- [ ] 队列有上限、用户有上限、过载返回稳定 429/503。
- [ ] 2 worker × 4 slots 通过 fencing 和副作用幂等测试。
- [ ] 排队和运行中任务都可取消，并正确释放容量与额度。
- [ ] Worker 强杀和 Redis 中断后无任务丢失、无重复终态。
- [ ] DB pool、锁等待、队列和 Worker slot 指标可观察。
- [ ] 模型 Reservation 跨 Redis 和恢复链路传播。
- [ ] 成功、失败、取消、中断均正确结算 usage 并释放余额。
- [ ] Provider 401/429/503 和 DB/Redis 故障不会造成用户 Session 401。
- [ ] 慢 SMS 不会显著拖慢登录和课堂请求。
- [ ] 后端、真实 HTTP、前端、故障注入和完整回归全部通过。
- [ ] staging 完整课堂时段稳定，无 Reservation 泄漏或容量计数漂移。
- [ ] 文档、部署配置、监控告警和回退手册已同步更新。
