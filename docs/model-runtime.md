# 模型抽象层

NLP Agent 从 `v0.14.0` 起使用显式 Provider Registry 和类型化模型路由。Coordinator、Worker、Memory 与 Compression 不再直接构造 DeepSeek/OpenAI SDK 对象。

## 五层配置

`configs/agent_config.yaml` 将模型配置拆成五层：

1. `providers`：连接协议、Endpoint、密钥环境变量和固定 Header；
2. `models`：真实模型 ID、上下文窗口、最大输出和能力声明；
3. `model_presets`：思考、生成、超时、重试和熔断策略；
4. `model_routes`：Coordinator、Worker、Utility 的默认主模型和 fallback 链；
5. `model_profiles`：学生可选择的模型档案，显式绑定同一 Provider 的 Coordinator、Worker 和 Utility preset。

Provider 和 Model 必须显式关联，不通过 URL 或模型名称猜测 Provider。模型档案也会在配置加载时校验三个 preset 是否属于其声明的 Provider，避免无意间要求其他厂商的密钥。

## 默认 DeepSeek 路由

```text
coordinator: coordinator-pro (V4 Pro/max) -> coordinator-fast (V4 Flash/high)
worker:      worker-flash (V4 Flash/high) -> worker-pro (V4 Pro/high)
utility:     utility-flash (V4 Flash/non-thinking)
```

Utility 用于记忆整理、Context Collapse 和 Auto Compact，避免为确定性摘要支付 Pro/max 的延迟与 Token。

DeepSeek 思考模式通过 `extra_body.thinking.type` 控制。内部统一 effort 枚举为 `none/low/medium/high/max`；DeepSeek Adapter 将 low、medium、high 映射为 `high`，将 max 映射为 `max`。思考模式下会丢弃无效的 temperature/top_p。

## Qwen 适配

Qwen 使用专用 `qwen` Adapter，而不是把兼容接口直接当作通用 OpenAI Provider。Adapter 根据 preset 写入 `extra_body.enable_thinking`；启用思考时同时设置 `preserve_thinking=true`，并将 Qwen3.8 Max 的内部 `high/max` effort 映射为厂商参数 `xhigh`。

流式和非流式响应中的 `reasoning_content` 都会保留。多轮请求会将历史 Assistant 消息的 `reasoning_content` 回传给 Qwen，避免开启 `preserve_thinking` 后丢失推理上下文。缓存 Token、推理 Token 和总用量仍归一化到统一 usage 字段。

Qwen 原生联网搜索是 preset 级的显式能力，不会随 Qwen Provider 全局开启。只有 `worker-qwen-web` 设置 `native_search.enabled=true`；Adapter 才会在 `extra_body` 中写入 `enable_search=true` 和 `search_options`。普通 `coordinator-qwen-max`、`worker-qwen-plus` 与 `utility-qwen-plus` 的请求保持不联网。

Coordinator 仅把最新、实时、新闻、价格、政策、版本或用户明确要求联网检索的问题路由到 `web_researcher`。该 Worker 固定使用 `qwen3.7-plus`、`forced_search=true` 和 `turbo` 策略；运行时禁止模型覆盖及其他 Profile 借用该 preset。它采用 one-shot 执行（单轮、无工具、无 Provider/Worker 重试、预算耗尽不再调用模型），从服务端限制单任务最多一次原生搜索。用户提供明确 URL 时改用 `web_reader`；它继承当前普通 Worker 模型且只获授 `web_fetch`，不会触发 Qwen 原生搜索。两个内建 Web Profile 不继承全局 Worker 工具授权，也不能被 Developer UI 覆盖。兼容模式的模型回答不保证包含结构化来源 URL，因此运行时不得补造引用。

## 学生端模型档案

学生端从 Settings API 动态读取 `default_model_profile` 和 `model_profiles`。公开数据只包含档案名称、展示标签、Provider 和可用状态，不会暴露 API Key。缺少对应 Provider 密钥的档案会显示为不可用并禁止选择。

选择结果通过用户设置中的 `model_profile` 持久化，后续 `chat.send` 会携带该值。Gateway 将它作为当前 turn 的本地上下文传递给 Executor 和 LangGraph；Coordinator、Worker、Utility 分别解析档案绑定的 preset，不修改进程级默认模型，也不会影响其他会话。

## 运行语义

`ResilientChatModel` 保持 LangChain 的 `bind_tools()`、`with_structured_output()`、`ainvoke()` 和 `astream()` 调用形态。普通 `ainvoke()` 内部也使用统一流式路径，确保首 Token、流空闲和总超时具有相同语义。

重试仅覆盖超时、连接错误、408/409/429 和 5xx。400/401/403/404、上下文超限、非法工具 Schema、余额和配额错误不会重试或 fallback。SDK 自带重试固定为 0，避免双重重试。

流式调用遵守：

- 首个可见 delta 前失败，可以重试或 fallback；
- 已输出正文、reasoning 或工具调用 delta 后失败，抛出 `StreamInterruptedError`；
- 不会把新模型从头生成的内容静默拼接到已有流；
- reasoning、content、tool argument 和 usage chunk 都能维持流活性。

Fallback 在配置加载时验证 streaming/tool-call 能力。上下文预算使用整条候选链的最小窗口，并为候选链最大输出预留空间。

## DeepSeek 工具调用

DeepSeek thinking 模式下，包含工具调用的 Assistant 消息必须回传 `reasoning_content`。`DeepSeekChatModel` 只为此类消息注入 reasoning；普通完成轮次不回传，从而保持请求前缀稳定。

工具调用仍使用 LangChain 标准 `AIMessage.tool_calls` 与 `AIMessageChunk.tool_call_chunks`，后续由 Tool Runtime 执行 Pydantic 参数校验。模型层不会修复或猜测可执行参数。

## KV Cache

DeepSeek 服务端 Context/KV Cache 自动启用，本地不保存 KV 数据。统一 usage 支持：

```text
input_tokens
output_tokens
total_tokens
cached_tokens / cache_read
cache_miss_tokens
reasoning_tokens
```

Provider 原始 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens` 会进入模型 Span、Trace 和每日指标。Worker 的固定协议与 SOP 和动态时间已拆成不同消息，以提高相同 Profile 的稳定前缀命中率。

## 扩展 Provider

新 Provider 只需实现 Adapter 并注册：

```python
from core.model_runtime.registry import global_provider_registry

global_provider_registry.register("custom", CustomAdapter)
```

Adapter 返回 LangChain 兼容 Chat Model，负责 Provider 请求参数和响应元数据差异；重试、fallback、熔断、观测和流式保护由 `ResilientChatModel` 统一负责。

## 观测事件

每次 Provider 尝试产生 `model.request` Span，包含 provider、model、preset、attempt、fallback index、thinking、reasoning effort、TTFT、Token 和缓存指标。

运行时还会产生：

```text
model.retry
model.failover
model.circuit_open
model.stream_interrupted
```

未来 Gateway/WebUI 继续通过 `ObservabilityService` 查询，无需理解具体 Provider SDK。

## 计量与 Token 配额交接 (Multi-Provider Metering & Quota Handoff)

模型层为下游计费与配额控制系统提供不可变的标准化用量上报协议。

### 1. 核心数据契约

- **`ModelIdentity`**: 描述模型调用的静态身份，包含 `provider`, `provider_model`, `model_profile`, `preset`, `route`, `pricing_key`, `context_window_tokens`, `max_output_tokens`。
- **`UsageAttributionContext`**: 溯源归属上下文，包含 `request_id`, `user_id`, `workspace_id`, `conversation_id`, `turn_id`, `reservation_id`, `worker_id`, `parent_operation_id`, `purpose` (`coordinator | worker | compact | memory | vision | evaluation | other`)。
- **`CanonicalTokenUsage`**: 标准化 Token 用量，包含 `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`, `source` (`provider | estimated | none`), `provider_response_id`。严格保证：
  - `cached_input_tokens + cache_write_input_tokens <= input_tokens`
  - `reasoning_output_tokens <= output_tokens`
  - `total_tokens = input_tokens + output_tokens`
- **`ModelInvocation`**: 单次 Provider 尝试的事实记录，包含由系统生成的 UUIDv4 `operation_id`、`identity`、`attribution`、`attempt`、`fallback_index` 和带 UTC 时区的 `started_at`。
- **`InvocationOutcome`**: 尝试结果状态 (`succeeded | failed | interrupted | cancelled`), `finish_reason`, `error_kind`, `completed_at`。

### 2. 用量上报生命周期

- 通过 `configure_global_model_usage_reporter()` 注册全局 Reporter（满足 `ModelUsageReporter` 异步协议）；每个会调用模型的进程都要在启动时注册。
- 每次 Provider 尝试均生成唯一的 `operation_id`，在尝试结束（成功、失败、可见中断或取消）后严格上报且仅上报一次。
- `ainvoke` 与 `astream` 共享统一生命周期，Structured Output 内部强制使用 `include_raw=True` 提取原始响应 usage 并上报，对外透明返回解析对象。
- 若配置了 Reporter 但未绑定归属上下文，在发起 Provider 调用前立即抛出 `MissingUsageAttributionError`。
- Reporter 写入失败会使模型调用失败，并停止后续 Retry/Fallback；不得静默丢失计量记录。

### 3. 标准化错误分类

`classify_model_error()` 产生规范的 `error_kind`：
- `upstream_provider_quota_exhausted`: 厂商配额不足/欠费（不可重试）
- `upstream_rate_limited`: 429 限流（可重试，支持 Retry-After）
- `upstream_context_length_exceeded`: 上下文超限（不可重试）
- `upstream_auth_failed`: 401/403 鉴权失败（不可重试）
- `upstream_model_unavailable`: 404 模型不可用（不可重试）
- `upstream_invalid_request`: 400/422 非法请求（不可重试）
- `upstream_timeout`: 超时（可重试）
- `upstream_connection_error`: 连接重置/网络中断（可重试）
- `upstream_overloaded`: 408/409/5xx 或厂商过载（可重试）
- `upstream_empty_response`: 空流或空响应（可重试）
- `upstream_unknown`: 未知异常（不可重试）
