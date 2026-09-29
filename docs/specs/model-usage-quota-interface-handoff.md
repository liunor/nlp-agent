# 模型用量计量与 Token 额度管理接口交接文档

## 1. 文档用途

本文交给“额度管理协作者”。它只描述已经由模型 Runtime 提供的稳定接口，以及额度模块必须完成的接入工作。

模型侧已经负责：

- 识别每一次真实 Provider 请求；
- 归一化不同厂商返回的 Token Usage；
- 为 Retry、Fallback、流式、Structured Output、取消和中断生成 Attempt 级事件；
- 提供稳定的 Reporter 协议；
- 提供模型身份、价格查找键和输入 Token 估算入口。

额度侧仍需负责：

- 用户、Workspace、套餐和额度规则；
- Reservation、Admission、扣减、退款或释放；
- Durable Reporter、数据库表、唯一索引和事务；
- Web 进程与独立 Worker 进程的启动注入；
- 跨 Redis 任务边界传播 `reservation_id`；
- 对账、未知 Usage 处理、管理 API 和可观测告警。

模型侧不会保存价格、余额或套餐规则，也不会直接写额度数据库。

## 2. 交付状态

- 验收分支：`feat/model-usage-quota-handoff`
- 验收基线提交：`cb2fdb0723bd4f113147e313061aa7ace91f1e61`
- 验收日期：2026-08-29
- 相关回归：156 passed
- Ruff：All checks passed

注意：验收时实现仍是工作树中的未提交改动，分支提交点与 `develop` 相同。正式交给额度协作者前，必须先形成提交，并把最终 commit SHA 一并提供。不能只提供分支名。

## 3. 稳定导入位置

额度模块只从以下位置导入公开契约：

```python
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelIdentity,
    ModelInvocation,
    ModelUsageReporter,
    UsageAttributionContext,
    bind_usage_attribution,
)
from core.model_runtime.reporters import (
    configure_global_model_usage_reporter,
)
from core.model_runtime.factory import get_global_model_factory
```

不要从 Provider Adapter、`runtime.py` 私有函数或 LangChain 内部类型读取计费数据。

## 4. Reporter 接口

额度侧实现以下异步协议：

```python
class ModelUsageReporter(Protocol):
    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        ...
```

启动时注册：

```python
reporter = DurableQuotaUsageReporter(...)
configure_global_model_usage_reporter(reporter)
```

必须在每个实际调用模型的进程中注册，至少包括：

- Web 进程使用 In-Process Executor 时的应用生命周期；
- `server.worker.runtime.run_worker()` 启动的独立 Worker 进程；
- 任何单独运行 Evaluation、Memory 或后台模型任务的进程。

只在 Web 进程注册，不能覆盖独立 Worker 进程。

Reporter 只允许在进程启动和关闭阶段配置，请求处理中不得切换。

## 5. 三个核心对象

### 5.1 `ModelInvocation`

一条 `ModelInvocation` 对应一次真实 Provider Attempt，不对应一个完整 Turn。

| 字段 | 类型 | 额度侧含义 |
|---|---|---|
| `operation_id` | `str` | UUIDv4，Attempt 的主幂等键 |
| `identity` | `ModelIdentity` | 实际命中的 Provider、模型和配置身份 |
| `attribution` | `UsageAttributionContext` | 用户、Workspace、Turn、Reservation 和用途 |
| `attempt` | 正整数 | 当前候选模型内的重试序号，从 1 开始 |
| `fallback_index` | 非负整数 | 候选模型序号，主模型为 0 |
| `started_at` | UTC datetime | Provider Attempt 开始时间 |

Retry 和 Fallback 会产生新的 `operation_id`。额度侧必须记录每个 Attempt，不能只记录最后一次成功调用。

### 5.2 `ModelIdentity`

| 字段 | 是否可空 | 含义 |
|---|---:|---|
| `provider` | 否 | Runtime Provider 名称，如 `deepseek`、`qwen` |
| `provider_model` | 否 | Provider 实际模型 ID |
| `model_profile` | 是 | 用户选择的模型档案；显式覆盖、Vision、Evaluation 可为空 |
| `preset` | 否 | 实际使用的 Preset |
| `route` | 是 | Runtime Route；直接 Preset 可为空 |
| `pricing_key` | 是 | 价格规则查找键 |
| `context_window_tokens` | 是 | 模型上下文上限 |
| `max_output_tokens` | 是 | 当前 Preset 输出上限 |

价格查找必须优先使用 `pricing_key`，不能使用展示名称或 `model_profile` 代替。

当前配置：

| Provider 模型 | `pricing_key` |
|---|---|
| `deepseek-v4-flash` | `deepseek/deepseek-v4-flash` |
| `deepseek-v4-pro` | `deepseek/deepseek-v4-pro` |
| `qwen3.8-max` | `qwen/qwen3.8-max` |
| `qwen3.7-plus` | `qwen/qwen3.7-plus` |
| `qwen3-vl-plus` | `qwen/qwen3-vl-plus` |

`pricing_key` 只标识价格规则，不包含单价和价格版本。价格版本应由额度侧根据事件时间或显式规则版本管理。

### 5.3 `UsageAttributionContext`

| 字段 | 是否可空 | 含义 |
|---|---:|---|
| `request_id` | 否 | 请求链路 ID |
| `user_id` | 否 | 额度归属用户；系统任务使用 `system` |
| `workspace_id` | 是 | Workspace 额度归属 |
| `conversation_id` | 是 | 会话 ID |
| `turn_id` | 是 | Turn ID |
| `reservation_id` | 是 | 额度侧预占记录 ID |
| `worker_id` | 是 | Worker ID |
| `parent_operation_id` | 是 | 第一阶段通常为空，不得用 Turn ID 冒充 |
| `purpose` | 否 | 调用用途 |

`purpose` 的合法值：

```text
coordinator
worker
compact
memory
vision
evaluation
other
```

普通 Coordinator 和 Worker 可从 Telemetry Context 自动解析归属。额度侧需要携带 `reservation_id` 时，应在调用边界显式绑定完整上下文：

```python
attribution = UsageAttributionContext(
    request_id=request_id,
    user_id=user_id,
    workspace_id=workspace_id,
    conversation_id=session_id,
    turn_id=turn_id,
    reservation_id=reservation_id,
    worker_id=worker_id,
    purpose="worker" if worker_id else "coordinator",
)

with bind_usage_attribution(attribution):
    result = await execute_model_turn(...)
```

模型实例会被全局缓存，因此禁止把用户、Workspace、Reservation 或 Turn 写入模型实例。

## 6. Canonical Token Usage

| 字段 | 含义 |
|---|---|
| `input_tokens` | Provider 报告的总输入 Token |
| `cached_input_tokens` | 输入中命中缓存的子集 |
| `cache_write_input_tokens` | 输入中用于写缓存的子集 |
| `output_tokens` | Provider 报告的总输出 Token |
| `reasoning_output_tokens` | 输出中 Reasoning Token 子集 |
| `total_tokens` | 固定等于 input + output |
| `source` | `provider`、`estimated` 或 `none` |
| `provider_response_id` | Provider 响应 ID，可空 |

严格不变量：

```text
cached_input_tokens + cache_write_input_tokens <= input_tokens
reasoning_output_tokens <= output_tokens
total_tokens == input_tokens + output_tokens
所有 Token 字段都是非负严格整数
bool、float、字符串不能作为 Token 数值
source == none 时所有 Token 字段必须为 0
```

计价时不要重复计算子集。通常应先得到：

```text
ordinary_input_tokens =
    input_tokens
    - cached_input_tokens
    - cache_write_input_tokens
```

然后按当前 `pricing_key` 对普通输入、缓存读取、缓存写入和输出分别应用价格规则。`reasoning_output_tokens` 已包含在 `output_tokens` 中，除非厂商价格规则明确要求拆分，否则不能再次累加。

`source="provider"` 且 Token 全为 0 是合法数据，表示 Provider 明确返回了全零 Usage。

`source="none"` 表示没有可靠 Usage，不表示这次请求一定免费。额度侧应记录为待对账或执行自己的保守策略，不能把它伪装成 Provider 精确零用量。

## 7. Outcome 与结算规则

`InvocationOutcome.status` 的合法值：

| 状态 | 含义 | 是否可能带 Provider Usage |
|---|---|---:|
| `succeeded` | Attempt 成功 | 是 |
| `failed` | Provider 或解析失败 | 是 |
| `interrupted` | 流已产生可见输出后中断 | 是 |
| `cancelled` | 调用被取消 | 是 |

额度侧不能只对 `succeeded` 结算。Provider 可能对失败、中断或取消前已经处理的 Token 收费。

建议规则：

1. 先按 `operation_id` 幂等落 Usage Event；
2. `source="provider"` 时按实际 Token 结算，不以 Outcome 成功与否决定是否记账；
3. `source="none"` 时记录未知用量状态，进入对账或保守策略；
4. 一个 Turn 的多个 Retry/Fallback Attempt 分别结算；
5. Turn 完成后释放未使用 Reservation，但不能删除已落地的 Attempt 事实。

`completed_at` 是 UTC 时间。持久化时必须保留 UTC 语义。

## 8. 幂等与事务要求

Durable Reporter 至少满足：

- `operation_id` 建唯一索引或作为主键；
- 相同 `operation_id`、相同内容重复上报时无操作成功；
- 相同 `operation_id`、不同内容重复上报时抛出冲突错误并告警；
- `provider + provider_response_id` 只能作为辅助对账键，不能替代 `operation_id`；
- Usage Event 与额度 Ledger 更新应处于同一数据库事务，或使用可靠 Outbox；
- Reporter 返回前，必须完成本次调用所要求的可靠持久化。

Reporter 失败不会被模型 Runtime 静默吞掉：

- Provider 失败后的 Reporter 失败会停止 Retry/Fallback；
- Provider 成功后的 Reporter 失败会使模型调用整体失败；
- 已经输出流内容后 Reporter 失败，流会以错误结束；
- Reporter 失败不能触发第二次 Provider 请求。

因此 Reporter 的错误必须清楚区分数据库不可用、幂等冲突和业务拒绝，方便上层告警和恢复。

## 9. Admission 与 Reservation 建议流程

模型侧提供两个查询入口：

```python
factory = get_global_model_factory()

identity = factory.profile_identity(model_profile, role)
estimated_input = factory.estimate_input_tokens(model_profile, messages)
```

`role` 只能是：

```text
coordinator
worker
utility
```

`estimate_input_tokens()` 当前复用项目的粗略消息估算器，可能返回 `None`。它不是 Provider 精确 Usage，也不是 `tiktoken cl100k_base` 的稳定计费结果。

建议调用流程：

```text
选择 model_profile 和 role
        ↓
读取 ModelIdentity 和 pricing_key
        ↓
估算输入与最大输出风险
        ↓
创建 Reservation / 执行 Admission
        ↓
显式绑定带 reservation_id 的 UsageAttributionContext
        ↓
执行模型 Turn（可能产生多个 Attempt）
        ↓
Durable Reporter 按 Attempt 落账
        ↓
Turn 结束后释放剩余 Reservation
```

如果估算返回 `None`，额度侧必须使用明确的降级策略，例如按模型最大风险预占或拒绝调用，不能把 `None` 当成 0。

## 10. 跨进程传播

In-Process 模式可以通过 ContextVar 绑定 Attribution。Redis Worker 模式不能依赖 Web 进程的 ContextVar，必须把所需字段写入任务契约并在 Worker 消费端重新绑定。

至少传播：

```text
request_id
user_id
workspace_id
conversation_id/session_id
turn_id
reservation_id
worker_id（执行 Worker 时）
purpose
```

额度协作者需要检查并扩展 Gateway Task、Redis 序列化和 Worker 恢复路径。任务重放必须沿用原 Reservation 语义，但每个真实 Provider Attempt 仍产生新的 `operation_id`。

## 11. Provider 错误分类

模型 Runtime 的 `error_kind`：

| `error_kind` | 是否重试 | 含义 |
|---|---:|---|
| `upstream_provider_quota_exhausted` | 否 | Provider 账户欠费或配额耗尽，包括 HTTP 402 和明确欠费代码 |
| `upstream_rate_limited` | 是 | 429 限流，支持 Retry-After |
| `upstream_context_length_exceeded` | 否 | 上下文超限 |
| `upstream_auth_failed` | 否 | 401/403 |
| `upstream_model_unavailable` | 否 | 404 或明确模型不可用 |
| `upstream_invalid_request` | 否 | 400/422 非上下文错误 |
| `upstream_timeout` | 是 | 超时 |
| `upstream_connection_error` | 是 | 连接中断或 Reset |
| `upstream_overloaded` | 是 | 408/409/5xx 或过载 |
| `upstream_empty_response` | 是 | 空响应或空流 |
| `upstream_unknown` | 否 | 未识别错误 |
| `structured_output_parse_error` | 否 | Provider 已响应，但结构化解析失败 |

必须保持以下区分：

```text
upstream_provider_quota_exhausted
    = 项目使用的 Provider 账户没有余额

quota_daily_exhausted / quota_weekly_exhausted / admission_denied
    = Pro_NLP 用户额度规则拒绝
```

模型 Runtime 不会生成 Pro_NLP 用户额度错误，额度侧不得把两类错误混为一谈。

## 12. 额度侧最低数据库能力

具体表名由额度模块决定，但必须能表达：

- 唯一 `operation_id`；
- Provider、Provider Model、Pricing Key、Profile、Preset、Route；
- User、Workspace、Conversation、Turn、Reservation、Worker、Purpose；
- Attempt、Fallback Index；
- 所有 Canonical Token 字段和 Usage Source；
- Provider Response ID；
- Outcome Status、Finish Reason、Error Kind；
- Started At、Completed At；
- 使用的价格规则版本；
- 最终金额或额度扣减明细；
- 对账状态和异常原因。

不要只存 `total_tokens`。缓存输入和 Reasoning 子集丢失后，无法按多厂商价格规则准确结算。

## 13. 接入验收清单

额度侧完成后，至少验证：

- [ ] 所有模型调用进程均注入同一个语义的 Durable Reporter；
- [ ] Reporter 未配置时生产启动失败，或有明确的启动健康检查；
- [ ] 缺少 Attribution 时 Provider 不会被调用；
- [ ] Retry 两次后成功产生三条不同 `operation_id` 记录；
- [ ] Fallback 记录的是实际命中的 Provider 和模型；
- [ ] 显式 Qwen Worker 不会带 DeepSeek `model_profile`；
- [ ] 流式成功、可见中断、取消均能落账；
- [ ] Structured Output 成功和解析失败均能取得原始 Usage；
- [ ] Provider 错误响应携带 Usage 时仍能落账；
- [ ] Reporter 失败不会发起第二次 Provider 请求；
- [ ] 同一 `operation_id` 重放不会重复扣减；
- [ ] 幂等冲突会告警，不会覆盖历史事实；
- [ ] `source="none"` 不会被当成免费精确零用量；
- [ ] 缓存 Token 和 Reasoning Token 不会重复计费；
- [ ] Redis Worker 能恢复 `reservation_id` 和用户归属；
- [ ] Provider 欠费和 Pro_NLP 用户额度不足显示为不同错误。

## 14. 模型侧验收命令

专项测试：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_model_usage_contracts.py `
  tests/test_llm_factory.py `
  tests/test_model_usage_providers.py `
  tests/test_model_usage_reporting.py `
  tests/test_model_runtime.py -q
```

扩展相关回归还包括：

```text
tests/test_memory_runtime.py
tests/test_vision_vlm.py
tests/test_worker_profiles.py
tests/test_worker_lifecycle.py
tests/test_gateway_core.py
tests/test_gateway_engine.py
tests/test_redis_transport.py
```

## 15. 不在本次模型侧交付中的内容

以下内容不是遗漏，而是额度模块的后续职责：

- Durable Reporter 的具体实现；
- Usage Event、Ledger、Reservation 的数据库迁移；
- 用户套餐、日/周额度、共享 Workspace 额度；
- Pricing Rule 管理和历史版本；
- Admission API 和面向用户的额度错误；
- 余额查询、管理后台、对账任务；
- 跨进程 Reservation 字段扩展；
- `parent_operation_id` 的完整调用链传播。

额度侧不得通过修改 Provider Adapter 来实现扣费。唯一接入点是 `ModelUsageReporter`、Attribution 绑定和 Factory 查询接口。
