# Pro_NLP 多厂商模型计量与 Token 额度对接实施设计

> 文档类型：模型管理侧实施规格 + 额度模块接口交接稿
>
> 适用代码基线：`develop` / `cb2fdb0723bd4f113147e313061aa7ace91f1e61`
>
> 适用对象：多厂商模型管理实现者、额度模块实现者、代码审查者
>
> 实施方式：可以拆给多个能力较弱的代码模型，但必须按本文的任务依赖、文件边界和验收项执行
>
> 状态：已实施完成（Model 侧已交付，接口交接完成；业务计费政策仍由额度协作者确认）

## 1. 这次实施要解决什么

本次实施只建立一条稳定、可测试的“模型实际调用计量链路”：

```text
一次真实 Provider 请求
    -> 明确实际 Provider、模型、Preset、Retry/Fallback 次数
    -> 把 Provider 原始 Usage 转成统一 CanonicalTokenUsage
    -> 关联用户、Workspace、Turn、Worker 和用途
    -> 通过 ModelUsageReporter 上报一次
    -> 由额度模块持久化 UsageEvent 并完成后续计价与结算
```

模型管理模块提供调用事实，不计算 Credits，也不修改用户余额。

本次实施完成后，额度协作者应当能够只依赖公开契约，不读取 DeepSeek、Qwen 或 LangChain 的原始响应结构。

## 2. 明确不在本次模型侧实施范围内的内容

以下内容归额度模块或 Gateway Admission 所有，本次模型侧任务不得自行实现：

- 用户、角色、Workspace 的额度政策；
- Token 到 Credits 或货币的换算；
- Pricing Version 的选择；
- Reservation、Settlement、Release；
- Bucket、Ledger、UsageSnapshot；
- 额度相关数据库表和 Alembic Migration；
- 额度不足的 HTTP/WebSocket 返回结构；
- 前端用量页面；
- 自动切换低成本模型的业务政策。

模型侧可以提供 `pricing_key`，但不能在 Provider Adapter 或 Runtime 中硬编码价格。

## 3. 当前项目事实

以下结论来自当前代码，不是对未来实现的猜测。

### 3.1 当前模型配置和路由

配置文件：`configs/agent_config.yaml`

当前生产配置的 Provider：

| Provider | Adapter | 当前模型 |
|---|---|---|
| DeepSeek | `deepseek` | `deepseek-v4-flash`、`deepseek-v4-pro` |
| Qwen | `qwen` | `qwen3.8-max`、`qwen3.7-plus`、`qwen3-vl-plus` |

`openai_compatible` Adapter 已注册，但当前没有对应的生产 Provider 配置。它只能视为扩展基础，不能在缺少 Usage 契约测试时宣称已完成计费接入。

当前 Model Profile：

| Profile | Coordinator | Worker | Utility |
|---|---|---|---|
| `deepseek` | `coordinator-pro` | `worker-flash` | `utility-flash` |
| `qwen` | `coordinator-qwen-max` | `worker-qwen-plus` | `utility-qwen-plus` |

视觉模型使用独立路由 `vision-worker -> vision-qwen-plus`，不属于上述 Profile 的三个角色字段。

### 3.2 当前统一模型出口

模型创建入口为：

- `core/model_runtime/factory.py::ModelFactory`
- `server/agent/llm_factory.py`

所有主要业务模型调用已经经过 `ResilientChatModel`：

| 调用类别 | 主要位置 | 当前模型出口 |
|---|---|---|
| Coordinator | `server/agent/node/coordinator.py` | `get_planner_llm()` |
| Worker | `server/tools/worker_tool.py` | `get_worker_llm()` / `get_tool_llm()` |
| Context Collapse | `server/agent/compression/context_collapse.py` | `get_utility_llm()` |
| Auto Compact | `server/agent/compression/auto_compact.py` | `get_utility_llm()` |
| Memory Curator | `server/memory/curator.py` | `get_utility_llm()` |
| Vision | `server/tools/vision/vlm.py` | `ModelFactory.build_route()` |
| Evaluation | `evaluation/**/student_simulator.py`、`evaluation/exercise_blueprint/judge.py` | `build_preset()` |

不得只覆盖 Coordinator。Worker、Utility、Vision、Evaluation 都是真实 Provider 调用。

### 3.3 当前 Runtime 行为

`core/model_runtime/runtime.py::ResilientChatModel` 当前已经负责：

- Provider 级重试；
- Fallback；
- 熔断；
- 首 Token、流空闲和总超时；
- 流已产生可见输出后禁止透明重放；
- 模型 Span 和重试/Fallback 观测事件。

普通 `ainvoke()` 实际上复用 `astream()`，所以普通文本调用和流式调用必须共享同一计量实现，不能各自上报造成重复。

### 3.4 当前 Usage 能力和缺口

当前已有：

- `core/model_runtime/normalization.py::normalize_usage()`；
- DeepSeek 的缓存命中/未命中字段归一化；
- Qwen 的缓存和 Reasoning 字段归一化；
- `usage_metadata` 和 `provider_usage`；
- Observability Span 中的 Token 展示。

当前缺失：

- 没有 `ModelIdentity`；
- 没有 `ModelInvocation` 和唯一 `operation_id`；
- 没有不可变 `CanonicalTokenUsage`；
- 没有 `ModelUsageReporter`；
- 没有 UsageEvent 持久化；
- Retry/Fallback 的每一次实际请求没有独立计量记录；
- 流式中断和失败调用没有最终计量结果；
- Provider Response ID 没有稳定暴露给模型 Runtime；
- Structured Output 返回解析对象时，原始 `AIMessage.usage_metadata` 会被隐藏；
- 没有按 `coordinator/worker/compact/memory/vision/evaluation` 标记用途。

### 3.5 为什么不能直接复用 Observability 作为额度账本

`core/observability/runtime.py` 使用非阻塞内存队列。队列满时允许丢弃事件，并且 Trace/Span 设计目标是性能观测，不是财务幂等账本。

因此必须保持：

```text
Observability Span   -> 可丢失的排障和性能数据
ModelUsageReporter   -> 不可静默丢失的业务 UsageEvent
```

两条链路可以共享 `operation_id`、Provider、模型和 Token 字段，但不能互相替代。

## 4. 已冻结的设计决定

低能力代码模型不得自行修改以下决定。

### D-01：一次实际 Provider 请求等于一个 Invocation

- 同一候选模型的第 1 次和第 2 次重试是两个 Invocation；
- Fallback 到另一个模型是新的 Invocation；
- 熔断器直接跳过候选模型时没有发出 Provider 请求，因此不创建 Invocation，不上报 Usage；
- 每个 Invocation 使用新的 UUIDv4 `operation_id`；
- Reporter 重放时必须沿用原 `operation_id`。

### D-02：Runtime 是唯一 UsageReporter 调用点

Reporter 只能由 `ResilientChatModel` 在一次 Provider 尝试结束时调用。

禁止在以下位置重复调用 Reporter：

- Provider Adapter；
- Coordinator；
- Worker；
- Memory、Compression 或 Vision 业务函数；
- Observability Runtime。

这些业务位置只负责设置 Attribution 的 `purpose`。

### D-03：Canonical 中 input 包含 cached，output 包含 reasoning

统一语义如下：

- `cached_input_tokens` 和 `cache_write_input_tokens` 都是 `input_tokens` 的子集，二者互不重叠；
- `reasoning_output_tokens` 是 `output_tokens` 的子集；
- `cache_write_input_tokens` 单独记录，不能塞进 `cached_input_tokens`；
- `total_tokens = input_tokens + output_tokens`；
- 价格模块不得把 cached 或 reasoning 在总量之外无条件再加一次。

如果某个新 Provider 的原始字段不满足上述语义，Adapter 必须先换算，再生成 Canonical。

### D-04：所有 Adapter 向 Runtime 暴露累计到当前的 Usage

Runtime 只保留一次 Invocation 中最新的非空 Usage 快照。

- Provider 原始流如果返回累计 Usage，Adapter 直接归一化；
- Provider 原始流如果返回增量 Usage，Adapter 必须先在 Adapter 内累加，再向 Runtime 暴露累计值；
- Runtime 不盲目把多个 chunk 的 Usage 相加。

当前 DeepSeek、Qwen 和 OpenAI-compatible 都按终止 chunk 的累计 Usage 处理。

### D-05：Structured Output 必须保留原始响应用于计量

`with_structured_output()` 内部必须强制使用 `include_raw=True` 获取：

```python
{
    "raw": AIMessage,
    "parsed": object,
    "parsing_error": Exception | None,
}
```

Runtime 从 `raw` 读取 Usage 并上报，然后：

- 原调用没有要求 `include_raw=True`：返回 `parsed`；
- 原调用要求 `include_raw=True`：返回完整字典；
- 有 `parsing_error`：完成本次 Usage 上报后重新抛出解析错误。

不得为了拿 Usage 改变 Memory Curator 或 Vision 的公开返回类型。

### D-06：Reporter 失败不得静默吞掉

- Reporter 在 Provider 失败后的上报失败：停止后续 Retry/Fallback，抛出 Reporter 错误；
- Reporter 在成功响应后的上报失败：模型调用整体失败；
- 流已经输出内容后 Reporter 失败：流最终以错误结束，不能把该调用标记为成功且零用量；
- Reporter 未配置时仅用于本地开发、原有单元测试和额度模块尚未接入的过渡阶段。

生产开启计量功能后必须注入 Durable Reporter。

### D-07：归属信息按调用读取，不绑定到缓存模型实例

`ModelFactory` 会缓存 `ResilientChatModel`，所以模型实例中不得保存用户、Workspace、Turn、Reservation 或 Worker。

这些信息必须在每次 Provider 尝试开始前从 ContextVar/Telemetry Context 中读取。

静态的 Profile、Route、Preset 可以保存在模型包装器或候选模型中。

### D-08：Model Profile 可以为空，但实际 Provider 和模型不能为空

以下调用可能没有用户选择的 Profile：

- `vision-worker` 独立路由；
- Evaluation 直接 `build_preset()`；
- 显式 Worker 模型覆盖；
- 未来系统后台任务。

因此 `ModelIdentity.model_profile` 为可空字段。禁止伪造 Profile 名称。

### D-09：现阶段不伪造 parent_operation_id

当前 Coordinator Tool 调用没有把产生该 Tool Call 的模型 Invocation ID 传入 Worker 配置。第一阶段允许 `parent_operation_id=None`。

如果后续要填充，必须显式把 `operation_id` 写入最终 `AIMessage.additional_kwargs`，并通过 Tool Runtime 传递。不得用 `turn_id` 冒充 `parent_operation_id`。

## 5. 稳定公开契约

新增文件：`core/model_runtime/usage.py`

该文件是模型模块和额度模块之间的稳定导入位置。配置类仍保留在 `core/model_runtime/contracts.py`。

### 5.1 类型定义

实现必须等价于以下契约。可以调整 import 和辅助方法，不得改变字段名称和语义。

```python
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


UsageSource = Literal["provider", "estimated", "none"]
UsagePurpose = Literal[
    "coordinator",
    "worker",
    "compact",
    "memory",
    "vision",
    "evaluation",
    "other",
]
InvocationStatus = Literal["succeeded", "failed", "cancelled", "interrupted"]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]


class UsageFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelIdentity(UsageFrozenModel):
    provider: str = Field(min_length=1)
    provider_model: str = Field(min_length=1)
    model_profile: str | None = None
    preset: str = Field(min_length=1)
    route: str | None = None
    pricing_key: str | None = None
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


class UsageAttributionContext(UsageFrozenModel):
    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    workspace_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    reservation_id: str | None = None
    worker_id: str | None = None
    parent_operation_id: str | None = None
    purpose: UsagePurpose


class CanonicalTokenUsage(UsageFrozenModel):
    input_tokens: StrictNonNegativeInt = 0
    cached_input_tokens: StrictNonNegativeInt = 0
    cache_write_input_tokens: StrictNonNegativeInt = 0
    output_tokens: StrictNonNegativeInt = 0
    reasoning_output_tokens: StrictNonNegativeInt = 0
    total_tokens: StrictNonNegativeInt = 0
    source: UsageSource = "none"
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_subsets_and_total(self) -> "CanonicalTokenUsage":
        if self.cached_input_tokens + self.cache_write_input_tokens > self.input_tokens:
            raise ValueError(
                "cached_input_tokens + cache_write_input_tokens "
                "must not exceed input_tokens"
            )
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning_output_tokens must be a subset of output_tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.source == "none" and any((
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_write_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
            self.total_tokens,
        )):
            raise ValueError("source=none cannot carry token values")
        return self


class ModelInvocation(UsageFrozenModel):
    operation_id: str = Field(min_length=1)
    identity: ModelIdentity
    attribution: UsageAttributionContext
    attempt: StrictPositiveInt
    fallback_index: StrictNonNegativeInt
    started_at: datetime

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        parsed = UUID(value)
        if parsed.version != 4:
            raise ValueError("operation_id must be a UUIDv4")
        return value

    @field_validator("started_at")
    @classmethod
    def validate_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("started_at must use UTC")
        return value


class InvocationOutcome(UsageFrozenModel):
    status: InvocationStatus
    finish_reason: str | None = None
    error_kind: str | None = None
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("completed_at must use UTC")
        return value


class ModelUsageReporter(Protocol):
    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        """Persist one Provider invocation idempotently by operation_id."""
```

### 5.2 时间要求

- `started_at`、`completed_at` 必须使用带时区的 UTC `datetime`；
- 禁止使用 `datetime.now()` 的 naive 时间；
- 推荐统一使用 `datetime.now(timezone.utc)`。

### 5.3 Reporter 幂等要求

额度模块实现 Reporter 时：

- 主幂等键为 `operation_id`；
- 相同 `operation_id`、相同内容重复上报，返回已有结果或无操作成功；
- 相同 `operation_id`、不同内容重复上报，抛出冲突错误；
- `provider + provider_response_id` 可以作为辅助唯一键，但不能替代 `operation_id`；
- Reporter 不得依据 Trace 是否存在决定是否写 UsageEvent。

额度模块计价时使用拆分后的非重叠数量：

```python
uncached_input_tokens = max(
    usage.input_tokens
    - usage.cached_input_tokens
    - usage.cache_write_input_tokens,
    0,
)

standard_output_tokens = max(
    usage.output_tokens - usage.reasoning_output_tokens,
    0,
)
```

如果价格规则没有独立 Reasoning Rate，则直接对全部 `output_tokens` 使用普通输出价格；
如果有独立 Reasoning Rate，才使用 `standard_output_tokens` 和
`reasoning_output_tokens` 分别计价。不得同时对全部 output 和 reasoning 再收费。

## 6. Attribution 上下文设计

### 6.1 上下文来源

项目已经有 `core/observability/context.py::TelemetryContext`，包含：

- `request_id`；
- `session_id`；
- `turn_id`；
- `workspace_id`；
- `user_id`；
- `worker_id`。

Usage Attribution 应复用这些调用上下文值，但不得依赖 Observability 是否成功持久化。

`core/model_runtime/usage.py` 需要提供：

```python
current_usage_attribution() -> UsageAttributionContext | None
bind_usage_attribution(context: UsageAttributionContext)
bind_usage_purpose(purpose: UsagePurpose)
resolve_usage_attribution() -> UsageAttributionContext
system_usage_attribution(*, purpose: UsagePurpose, request_id: str | None = None)
```

解析优先级：

1. 显式 `bind_usage_attribution()`；
2. 当前 `TelemetryContext` 转换；
3. Reporter 已配置但仍无法解析时，抛出 `MissingUsageAttributionError`，并且在发出 Provider 请求前失败。

`bind_usage_purpose()` 是对当前完整 Attribution 的局部覆盖，退出 context manager 后必须恢复原用途。

### 6.2 Telemetry 到 Attribution 的字段映射

| Attribution | 当前来源 |
|---|---|
| `request_id` | `TelemetryContext.request_id` |
| `user_id` | `TelemetryContext.user_id` |
| `workspace_id` | `TelemetryContext.workspace_id` |
| `conversation_id` | `TelemetryContext.session_id` |
| `turn_id` | `TelemetryContext.turn_id` |
| `reservation_id` | 第一阶段为 `None`；额度 Admission 接入后显式绑定 |
| `worker_id` | `TelemetryContext.worker_id` |
| `parent_operation_id` | 第一阶段为 `None` |
| `purpose` | 由调用位置设置；未设置时根据 Worker 上下文选择 `worker`，否则 `coordinator` |

### 6.3 Purpose 设置位置

| Purpose | 必须设置的位置 |
|---|---|
| `coordinator` | 默认的 Coordinator 模型调用 |
| `worker` | Worker Span 内的模型调用；包括 `web_researcher` |
| `compact` | `_generate_global_summary()`、`_generate_span_summary()` |
| `memory` | `MemoryCurator.curate()` 中的模型调用 |
| `vision` | `ModelRuntimeVLMProvider.analyze()` 的模型调用 |
| `evaluation` | Evaluation Simulator 和 Judge 的直接模型调用 |
| `other` | 仅限尚未分类的系统调用，不得作为方便的默认值长期使用 |

### 6.4 无用户后台调用

模型侧固定保留 `user_id="system"` 作为系统成本哨兵值。`system_usage_attribution()`：

- 固定 `user_id="system"`；
- 默认 `workspace_id=None`；
- 为每次独立调用生成 request ID；
- 必须由调用方显式给出 purpose；
- 不得被普通 Gateway 用户调用路径自动使用。

Evaluation 的直接 Provider 调用使用这个辅助函数。额度侧 Durable Reporter 必须在写用户额度表前识别
`user_id="system"` 并路由到系统成本中心，不能把它当作真实用户外键。

### 6.5 Reservation 接入约定

额度模块进入 Gateway Admission 后，需要：

1. Reserve 成功得到 `reservation_id`；
2. 把它加入 `TurnTask`；
3. 更新 `gateway/redis_transport.py::TurnTaskCodec.VERSION`；
4. Redis Worker 解码后仍能获得相同 `reservation_id`；
5. 执行 Turn 时通过 `bind_usage_attribution()` 绑定。

这部分由额度/Gateway 协作者实现，模型侧第一阶段不得提前伪造 Reservation。

## 7. Model Identity 和 Profile 查询

### 7.1 配置改动

修改 `core/model_runtime/contracts.py::ModelDefinition`：

```python
class ModelDefinition(FrozenModel):
    provider: str
    model_id: str
    pricing_key: str | None = None
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
```

修改 `configs/agent_config.yaml`，为当前五个模型添加稳定 `pricing_key`：

```yaml
pricing_key: deepseek/deepseek-v4-flash
pricing_key: deepseek/deepseek-v4-pro
pricing_key: qwen/qwen3.8-max
pricing_key: qwen/qwen3.7-plus
pricing_key: qwen/qwen3-vl-plus
```

`pricing_key` 是价格规则查找键，不是价格，也不能包含某次生效价格版本。

### 7.2 Runtime 中的实际 Identity

每次尝试根据当前 `ModelCandidate` 构造：

```text
provider              <- candidate.provider_name
provider_model        <- candidate.definition.model_id
model_profile         <- ResilientChatModel 的静态 Profile，可空
preset                <- candidate.preset_name
route                 <- ResilientChatModel 的静态 Route，可空
pricing_key           <- candidate.definition.pricing_key
context_window_tokens <- candidate.definition.context_window_tokens
max_output_tokens     <- candidate.preset.generation.max_output_tokens
```

不得使用展示名称作为 `model_profile`，不得把配置中的 model key 冒充 Provider 实际模型 ID。

### 7.3 Factory 必须保留 Profile/Route

修改 `ModelFactory` 和 `ResilientChatModel`：

- `build_route(route_name, *, model_profile=None)` 把 `route_name` 保存在包装器；
- `build_preset(preset_name, *, model_profile=None)` 把可选 Profile 保存在包装器；
- 新增 `build_profile_role(profile_name, role)`，内部解析 Profile 绑定的 Preset，并保留真实 `profile_name`；
- `bind_tools()` 和 `with_structured_output()` 创建新包装器时，必须保留 Reporter、Profile 和 Route；
- 缓存 key 必须包含 Profile/Route，不能让不同 Profile 错误复用 Identity 元数据。

Reporter 使用共享可变引用，而不是把某个 Reporter 副本散落到所有缓存包装器。建议在
`core/model_runtime/reporters.py` 定义 `ModelUsageReporterSlot`：

```python
class ModelUsageReporterSlot:
    def __init__(self, reporter: ModelUsageReporter | None = None) -> None:
        self.reporter = reporter

    def configure(self, reporter: ModelUsageReporter | None) -> None:
        self.reporter = reporter
```

`ModelFactory`、`ResilientChatModel`、`bind_tools()` 和 `with_structured_output()`
必须共享同一个 Slot。这样应用启动时配置 Reporter 后，之前产生的缓存包装器也不会继续
使用旧 Reporter。

公开配置入口固定为：

```python
def configure_global_model_usage_reporter(
    reporter: ModelUsageReporter | None,
) -> None:
    get_global_model_factory().reporter_slot.configure(reporter)
```

该函数只在应用/Worker 启动阶段调用；请求处理中不得切换 Reporter。

修改 `server/agent/llm_factory.py`，Profile 选择调用 `build_profile_role()`，不要继续先解析 Preset 再丢失 Profile 信息。

### 7.4 提供给额度 Admission 的查询能力

`ModelFactory` 新增：

```python
def profile_identity(self, profile_name: str, role: str) -> ModelIdentity: ...

def estimate_input_tokens(
    self,
    model_profile: str,
    messages: list[object],
) -> int | None: ...
```

第一阶段估算直接复用 `utils.tokens.rough_estimation_for_messages()`：

- 返回值只用于 Reservation 和上下文保护；
- 不能写成 `source=provider`；
- 无法安全估算时允许返回 `None`；
- 最终 Settlement 不能使用该估算覆盖 Provider Usage。

## 8. Canonical Usage 归一化

### 8.1 修改原则

保留现有 `normalize_usage()`，避免破坏 LangChain Message 和 Observability；新增一个明确返回 `CanonicalTokenUsage` 的函数，例如：

```python
def canonical_usage(
    metadata: Mapping[str, Any] | None,
    *,
    provider_response_id: str | None = None,
    source: UsageSource | None = None,
) -> CanonicalTokenUsage: ...
```

`normalize_usage()` 和 `canonical_usage()` 必须共享同一套基础字段提取，不能各写一份逐渐分叉的映射代码。

### 8.2 数据校验规则

- `bool` 不能作为合法 Token 整数；
- 负数、浮点数、非数字字符串不能静默传给额度模块；
- Provider Usage 存在但字段缺失时，缺失字段按 0；
- Provider Usage 完全不存在时，生成全 0、`source="none"`；
- Provider 明确返回全 0 Usage 时，允许 `source="provider"`；
- `cached_input_tokens` 大于 `input_tokens` 时 Adapter 必须拒绝该 Usage；
- `reasoning_output_tokens` 大于 `output_tokens` 时 Adapter 必须拒绝该 Usage；
- Canonical `total_tokens` 由 `input_tokens + output_tokens` 计算，不信任不一致的原始总数；
- 原始不一致总数可以写入 Observability 属性，但不能进入计价契约。

不得继续使用简单的 `int(value)` 接受 `"12"`、`1.5` 或 `True`。

### 8.3 Provider Response ID

Provider Adapter 必须把原始响应顶层 `id` 保存为稳定字段：

```text
AIMessage.response_metadata["provider_response_id"]
AIMessageChunk.response_metadata["provider_response_id"]
```

读取时优先使用 `provider_response_id`，其次兼容非流式 `response_metadata["id"]`。

禁止把 LangChain 自动生成的 `message.id` 或 Run ID 当成 Provider Response ID。

## 9. 当前 Provider 字段映射

### 9.1 DeepSeek

| Canonical | DeepSeek 原始字段 | 规则 |
|---|---|---|
| `input_tokens` | `prompt_tokens` | 包含缓存命中 Token |
| `cached_input_tokens` | `prompt_cache_hit_tokens` | input 子集 |
| `cache_write_input_tokens` | 当前不可用 | 0 |
| `output_tokens` | `completion_tokens` | 标准化后包含 Reasoning |
| `reasoning_output_tokens` | `completion_tokens_details.reasoning_tokens` 或现有兼容字段 | output 子集；缺失为 0 |
| `total_tokens` | 不直接采用 | 计算 input + output |
| `provider_response_id` | 响应顶层 `id` | Adapter 显式保留 |

`prompt_cache_miss_tokens` 继续进入 Observability，但不新增到 Canonical，因为可通过实际输入和缓存字段处理，且它不是独立计费加项。

### 9.2 Qwen

| Canonical | Qwen OpenAI-compatible 字段 | 规则 |
|---|---|---|
| `input_tokens` | `prompt_tokens` | 包含 cached |
| `cached_input_tokens` | `prompt_tokens_details.cached_tokens` | input 子集 |
| `cache_write_input_tokens` | 当前不可用 | 0 |
| `output_tokens` | `completion_tokens` | 包含 Reasoning |
| `reasoning_output_tokens` | `completion_tokens_details.reasoning_tokens` | output 子集 |
| `total_tokens` | 不直接采用 | 计算 input + output |
| `provider_response_id` | 响应顶层 `id` | Adapter 显式保留 |

### 9.3 Generic OpenAI-compatible

使用与 Qwen 相同的标准字段作为最低契约，但每个新 Provider 上线前必须增加自己的 Adapter 契约测试。

禁止因为 Endpoint “兼容 OpenAI”就假设 Cache、Reasoning 和流式 Usage 的语义完全一致。

### 9.4 失败响应中的 Usage

Runtime/Adapter 应保守读取以下可能位置：

- `error.body["usage"]`；
- `error.body["error"]["usage"]`；
- SDK Response 可安全解析出的 `usage`。

没有可靠 Usage 时使用 `source="none"`，不得写成 `provider` 的精确零用量。

## 10. Runtime 上报算法

### 10.1 通用尝试开始

在确认候选模型没有被熔断跳过后、发出 Provider 请求前：

1. 如果配置了 Reporter，解析 Attribution；解析失败立即停止，不能调用 Provider；
2. 生成 UUIDv4 `operation_id`；
3. 构造 `ModelInvocation`；
4. 创建 `model.request` Span，并把 `operation_id` 写入 Span attributes；
5. 开始 Provider 调用。

### 10.2 非结构化流式路径

每个 Provider 尝试维护本地变量：

```text
latest_usage = source=none
finish_reason = None
provider_response_id = None
received = False
visible = False
```

每收到一个 chunk：

1. Adapter/Normalization 归一化 chunk；
2. 如果 chunk 有 Provider Usage，用它替换 `latest_usage`；
3. 保存最新 `provider_response_id` 和 `finish_reason`；
4. 更新 Span；
5. 向上游 yield chunk。

终止处理：

| 情况 | Outcome | 上报后动作 |
|---|---|---|
| 正常完成 | `succeeded` | 返回 |
| Provider 错误且无可见输出 | `failed` | 按 ErrorDecision 决定 Retry/Fallback |
| Provider 错误且已有可见输出 | `interrupted` | 抛 `StreamInterruptedError`，禁止重放 |
| `asyncio.CancelledError` | `cancelled` | 上报后原样重新抛出 |
| 空流 | `failed` / `empty_response` | 可按现有策略重试 |

每种情况都只能调用 Reporter 一次。

### 10.3 普通 ainvoke

普通文本 `ainvoke()` 继续通过 `astream()` 合并消息。

Reporter 已在 `astream()` 完成，`ainvoke()` 不得第二次上报。

### 10.4 Structured Output 路径

每个候选模型使用内部 `include_raw=True` Runnable：

1. 调用得到 `{raw, parsed, parsing_error}`；
2. 从 `raw` 生成 Canonical Usage；
3. Reporter 上报一次；
4. 有解析错误则抛出；
5. 否则按调用者原始 `include_raw` 选项返回结果。

Structured Output 同样遵守 Retry、Fallback、Cancelled 和 Reporter 失败规则。

### 10.5 Reporter 调用辅助函数

建议在 `ResilientChatModel` 中实现一个私有方法统一上报：

```python
async def _report_attempt(
    self,
    *,
    invocation: ModelInvocation,
    usage: CanonicalTokenUsage,
    status: InvocationStatus,
    finish_reason: str | None,
    error_kind: str | None,
) -> None: ...
```

所有成功、失败、中断和取消分支都调用这个方法，避免遗漏。

## 11. Provider 错误分类

修改 `classify_model_error()`，对外使用稳定错误种类：

| 稳定 `error_kind` | 典型条件 | Retry/Fallback |
|---|---|---|
| `upstream_provider_quota_exhausted` | insufficient quota、余额不足、billing | 否 |
| `upstream_rate_limited` | 429 且不是余额不足 | 是，遵守 Retry-After |
| `upstream_context_length_exceeded` | 上下文超长 | 否 |
| `upstream_auth_failed` | 401/403 | 否 |
| `upstream_model_unavailable` | 404 model、明确模型不可用 | 否或由明确规则决定；第一阶段为否 |
| `upstream_invalid_request` | 400/422 非上下文错误 | 否 |
| `upstream_timeout` | Timeout | 是 |
| `upstream_connection_error` | 连接中断、Reset | 是 |
| `upstream_overloaded` | 408/409/5xx/temporarily unavailable | 是 |
| `upstream_empty_response` | 无任何 chunk | 是 |
| `upstream_unknown` | 无法识别 | 否 |

必须保持以下区分：

```text
Provider 账户额度不足 != Pro_NLP 用户额度不足
```

模型 Runtime 不得生成 `quota_daily_exhausted` 等用户额度错误。

## 12. 文件级修改清单

### 12.1 必须新增

| 文件 | 内容 |
|---|---|
| `core/model_runtime/usage.py` | 稳定契约、Protocol、Attribution ContextVar 和辅助函数 |
| `core/model_runtime/reporters.py` | 幂等 `InMemoryModelUsageReporter`，仅供测试/本地验证 |
| `tests/test_model_usage_contracts.py` | Canonical 校验和上下文测试 |
| `tests/test_model_usage_reporting.py` | Runtime 成功、失败、流式、Retry/Fallback、Structured Output 上报测试 |
| `tests/test_model_usage_providers.py` | DeepSeek、Qwen、OpenAI-compatible 字段映射测试 |

### 12.2 必须修改

| 文件 | 修改内容 |
|---|---|
| `core/model_runtime/contracts.py` | `ModelDefinition.pricing_key` |
| `configs/agent_config.yaml` | 五个模型的 `pricing_key` |
| `core/model_runtime/normalization.py` | Canonical 生成、严格整数校验、Provider Response ID 提取 |
| `core/model_runtime/runtime.py` | Invocation 生命周期和唯一 Reporter 调用点 |
| `core/model_runtime/factory.py` | Reporter 注入、Profile/Route 保留、Profile 查询和估算 |
| `core/model_runtime/adapters/deepseek.py` | 保留响应 ID，补齐 Canonical 原始字段 |
| `core/model_runtime/adapters/qwen.py` | 保留响应 ID，补齐 Canonical 原始字段 |
| `core/model_runtime/adapters/openai_compatible.py` | 使用可保留响应 ID 的专用子类 |
| `core/model_runtime/__init__.py` | 重导出稳定契约（可选，但不得造成循环导入） |
| `server/agent/llm_factory.py` | Profile 选择改用 `build_profile_role()` |
| `server/agent/compression/auto_compact.py` | LLM 调用绑定 `purpose="compact"` |
| `server/agent/compression/context_collapse.py` | LLM 调用绑定 `purpose="compact"` |
| `server/memory/curator.py` | LLM 调用绑定 `purpose="memory"` |
| `server/tools/vision/vlm.py` | LLM 调用绑定 `purpose="vision"` |
| `evaluation/**/student_simulator.py` | 直接模型调用绑定 `purpose="evaluation"` |
| `evaluation/exercise_blueprint/judge.py` | Judge 调用绑定 `purpose="evaluation"` |
| `docs/model-runtime.md` | 更新公开模型计量语义 |

### 12.3 第一阶段禁止修改

- `gateway/mysql_repository.py`；
- `server/infrastructure/mysql/models.py`；
- `migrations/versions/**`；
- `webui/**`；
- 用户、角色和 Workspace 管理代码。

这些文件属于额度模块后续阶段。模型侧 PR 如果修改它们，必须先说明为什么无法通过公开 Interface 完成。

## 13. 面向多个低能力模型的任务拆分

每个任务只允许修改列出的文件。完成一个任务后必须先通过该任务测试，再交给下一任务。

### M00：基线保护

依赖：无。

操作：

1. 确认分支基于本文记录的 develop 基线或更新文档中的 commit；
2. 确认工作区无他人未提交修改；
3. 运行当前模型相关测试并记录结果；
4. 不修改业务代码。

验收命令：

```powershell
uv run pytest tests/test_model_runtime.py tests/test_llm_factory.py -q
```

### M01：稳定 Usage 契约

依赖：M00。

允许修改：

- 新增 `core/model_runtime/usage.py`；
- 新增 `core/model_runtime/reporters.py`；
- 新增 `tests/test_model_usage_contracts.py`。

必须完成：

- 本文第 5 节全部契约；
- UTC 时间校验；
- ContextVar 嵌套绑定后正确恢复；
- Telemetry Context 转换；
- `system_usage_attribution()` 只生成保留的系统成本归属；
- InMemory Reporter 按 operation ID 幂等；
- 相同 operation ID 内容冲突时失败；
- Reporter Slot 配置后，所有持有该 Slot 的对象读取到同一个 Reporter。

禁止：

- 修改 Runtime；
- 添加数据库；
- 添加 Credits 字段。

### M02：配置、Identity 和估算

依赖：M01。

允许修改：

- `core/model_runtime/contracts.py`；
- `configs/agent_config.yaml`；
- `core/model_runtime/factory.py`；
- `core/model_runtime/runtime.py`，但本任务只允许增加静态 Profile/Route/Reporter Slot 字段并在包装器复制时保留，禁止实现上报流程；
- `server/agent/llm_factory.py`；
- `tests/test_llm_factory.py`；
- 必要时新增专用 Factory 测试。

必须完成：

- `pricing_key`；
- `build_profile_role()`；
- Profile/Route 的缓存 key；
- `profile_identity()`；
- `estimate_input_tokens()`；
- `bind_tools()`/Structured 包装后 Identity 元数据不会丢失所需的 Factory 参数。

验收重点：

- DeepSeek Profile 返回 DeepSeek Identity；
- Qwen Profile 返回 Qwen Identity；
- Vision Route 的 `model_profile is None`；
- 显式 Qwen Web Worker 不被错误标记为 DeepSeek Profile；
- 不在模型实例中存储用户 ID。

### M03：Provider Usage 归一化

依赖：M01。

允许修改：

- `core/model_runtime/normalization.py`；
- 三个 Provider Adapter；
- 新增 `tests/test_model_usage_providers.py`；
- 调整 `tests/test_model_runtime.py` 中直接相关测试。

必须完成：

- 严格 Token 整数校验；
- Canonical 子集和 total 语义；
- DeepSeek 映射；
- Qwen 映射；
- Generic OpenAI-compatible 映射；
- 非流式和流式 Response ID；
- 无 Usage、全零 Usage、非法 Usage 测试；
- 原有 `usage_metadata` 和 `provider_usage` 行为不回归。

禁止：

- 在 Adapter 中调用 Reporter；
- 修改 Retry/Fallback。

### M04：Runtime Invocation 和 Reporter

依赖：M01、M02、M03。

允许修改：

- `core/model_runtime/runtime.py`；
- `core/model_runtime/factory.py` 中仅限 Reporter 传递；
- 新增 `tests/test_model_usage_reporting.py`；
- 调整 `tests/test_model_runtime.py`。

必须完成：

- 每次实际尝试 UUIDv4；
- 非流式成功只上报一次；
- 流式成功只上报一次；
- Retry 每次独立上报；
- Fallback 每次独立上报；
- 失败带 Usage 时上报；
- 无 Usage 时上报 `source=none`；
- Cancelled 上报；
- 已有可见输出的中断上报 `interrupted`；
- Reporter 失败阻止后续调用；
- operation ID 写入模型 Span；
- Reporter 未配置时保持原项目行为。

禁止：

- 在 `ainvoke()` 和 `astream()` 对同一次调用各报一次；
- 捕获 `CancelledError` 后不重新抛出；
- 把多个累计 Usage chunk 相加。

### M05：Structured Output 计量

依赖：M04。

允许修改：

- `core/model_runtime/runtime.py`；
- `tests/test_model_usage_reporting.py`；
- 现有 Vision/Memory 测试的 Fake Model。

必须完成：

- 内部 `include_raw=True`；
- 从 raw message 计量；
- 对外返回类型不变；
- parsing error 也先上报 Usage；
- Structured Retry/Fallback 独立上报；
- `bind_tools().with_structured_output()` 和反向组合不丢 Reporter/Identity。

### M06：Purpose 覆盖全部模型调用

依赖：M05。

允许修改：第 12.2 节列出的 Compression、Memory、Vision、Evaluation 文件及其测试。

必须完成：

- Coordinator 默认；
- Worker 自动带 worker ID 和 `purpose=worker`；
- Compact 两个入口；
- Memory；
- Vision；
- Evaluation。

每个类别至少有一个测试捕获 Reporter 事件并断言 `purpose`。

禁止：

- 根据模型名称猜用途；
- 把 Vision 记为普通 Worker；
- 把 Memory 记为 Compact。

### M07：Provider 错误分类

依赖：M05。M07 与 M05 都修改 `runtime.py`，必须顺序执行，不能并行。

允许修改：

- `core/model_runtime/runtime.py`；
- `tests/test_model_runtime.py`；
- `tests/test_model_usage_reporting.py`。

必须覆盖第 11 节全部错误类型，至少测试：

- Provider 余额不足不重试；
- 普通 429 可以重试；
- 401/403 不重试；
- 上下文超长不重试；
- 503 可以重试/Fallback；
- 错误 Outcome 中保存稳定 `error_kind`。

### M08：文档和总回归

依赖：M02 至 M07。

允许修改：

- `docs/model-runtime.md`；
- 本文档中的实现状态；
- 仅为修复回归所需的相关测试。

必须完成：

- 运行第 15 节全部测试；
- 搜索所有直接模型构造和调用，确认没有漏网生产出口；
- 检查 Git Diff，不包含额度数据库和前端实现；
- 把实际偏差回写到本文，不允许代码和文档不一致。

### 13.1 推荐执行顺序

为了避免多个低能力模型同时覆盖同一文件，按以下顺序合并：

```text
M00
  -> M01
  -> M02 和 M03（可以并行，但 M02 独占 factory/runtime，M03 独占 normalization/adapters）
  -> 先合并 M02、M03
  -> M04
  -> M05
  -> M06
  -> M07
  -> M08
```

M04、M05、M07 都修改 `core/model_runtime/runtime.py`，必须由同一个连续任务链顺序完成。
如果使用不同代码模型，后一个模型必须基于前一个模型已经合并后的提交开始。

### 13.2 分发给代码模型的统一任务前缀

给每个实现模型的提示词前面都添加：

```text
你正在实现《docs/specs/model-usage-quota-handoff-implementation.md》中的单个任务包。
只执行指定任务包，不提前实现后续任务，不修改“禁止修改”的文件。
先读取任务涉及的现有代码和测试，再编辑。
不得删除或放宽现有测试，不得访问真实 Provider，不得加入价格和额度逻辑。
完成后必须列出：修改文件、契约对应关系、运行的命令、测试结果、尚未验证的风险。
如果文档与实际代码冲突，停止扩展范围，只报告冲突给集成者决定。
```

## 14. 测试规格

### 14.1 Contract 测试

- 负 Token 被拒绝；
- 浮点、字符串、bool 被拒绝；
- cached 与 cache write 之和大于 input 被拒绝；
- reasoning 大于 output 被拒绝；
- total 不等于 input + output 被拒绝；
- `source=none` 携带非零 Token 被拒绝；
- Provider 返回全 0 Usage 时允许 `source=provider`；
- Attribution 嵌套绑定退出后恢复父上下文。

### 14.2 Adapter 测试

- DeepSeek cache hit 正确；
- DeepSeek cache miss 仍保留给 Observability；
- Qwen cached 和 reasoning 正确；
- Response ID 在非流式和流式中都保留；
- 无 Usage 不伪装成精确零；
- 原始 total 不一致不会污染 Canonical total；
- 新 Provider 未写契约测试不能加入生产配置。

### 14.3 Runtime 测试

使用 Fake Provider Model 和 `InMemoryModelUsageReporter`，不得访问真实外网。

必测矩阵：

| 场景 | Provider 请求数 | UsageEvent 数 | 结果 |
|---|---:|---:|---|
| 非流式成功 | 1 | 1 | succeeded |
| 流式成功 | 1 | 1 | succeeded |
| 空流后成功重试 | 2 | 2 | failed + succeeded |
| 503 两次后 Fallback 成功 | 3 | 3 | failed + failed + succeeded |
| 400 非重试错误 | 1 | 1 | failed |
| 可见 chunk 后连接中断 | 1 | 1 | interrupted |
| 调用被取消 | 1 | 1 | cancelled |
| Reporter 在第一次失败上报时失败 | 1 | 0 或 Reporter 自身失败记录 | 不得发起第二次 Provider 请求 |
| Structured 成功 | 1 | 1 | succeeded |
| Structured 解析失败 | 1 | 1 | Usage 已报，解析错误抛出 |

每条记录还要断言：

- 不同实际请求的 `operation_id` 不同；
- Retry 的 `attempt` 从 1 递增；
- Fallback 的 `fallback_index` 从 0 递增；
- Fallback 新候选的 `attempt` 从 1 重新开始；
- Identity 是实际命中模型，不是最初计划模型；
- `started_at <= completed_at`；
- Provider Response ID 正确。

### 14.4 Call-site Attribution 测试

至少断言：

- Coordinator：正确 user/workspace/turn，worker 为空；
- Worker：沿用父 user/workspace/turn，worker ID 正确；
- Compact：purpose 正确；
- Memory：purpose 正确；
- Vision：purpose 正确；
- Evaluation：purpose 正确，进入系统成本归属而不是伪造普通学生。

### 14.5 不允许的测试方式

- 不允许只断言函数被调用，不检查 Reporter 内容；
- 不允许 Mock 掉整个 `ResilientChatModel` 后声称完成 Runtime 验收；
- 不允许依赖真实 Provider API Key；
- 不允许通过 `sleep` 猜异步任务已经完成；
- 不允许删除现有 Retry、Streaming 或 Vision 测试来让新测试通过。

## 15. Windows / PowerShell 验收命令

项目标准命令：

```powershell
uv sync
uv run pytest tests/test_model_usage_contracts.py -q
uv run pytest tests/test_model_usage_providers.py -q
uv run pytest tests/test_model_usage_reporting.py -q
uv run pytest tests/test_model_runtime.py tests/test_llm_factory.py -q
uv run pytest tests/test_agent_runtime.py tests/test_coordinator_runtime.py -q
uv run pytest tests/test_worker_profiles.py tests/test_worker_recovery.py -q
uv run pytest tests/test_vision_vlm.py tests/test_observability.py -q
uv run ruff check core/model_runtime server/agent/llm_factory.py server/tools/worker_tool.py server/memory server/tools/vision evaluation tests
```

最终回归：

```powershell
uv run pytest -q
```

如本机 `uv` 或现有 `.venv` 不可用，必须在交付记录中写明“未执行测试”和具体环境错误，不能把代码阅读结果写成测试通过。

## 16. 额度协作者可以依赖的接口

模型侧完成 M01 至 M08 后，额度模块只需要实现：

```python
class DurableModelUsageReporter(ModelUsageReporter):
    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        ...
```

并在应用/Worker 启动、任何模型创建之前注入 `ModelFactory`。

Web 进程和独立 Redis Worker 是不同进程，必须各自执行一次
`configure_global_model_usage_reporter()`。只在 Web 进程配置不会自动影响 Worker。

额度模块不得：

- import DeepSeek/Qwen Adapter；
- 读取 `AIMessage.response_metadata`；
- 读取 Observability 表作为结算依据；
- 在 Adapter 内写 UsageEvent；
- 要求模型实例绑定用户；
- 用 `total_tokens` 作为唯一计价输入。

## 17. 额度侧后续接入时序

### Phase A：契约测试

- 模型侧使用 InMemory Reporter；
- 额度侧使用 InMemory Usage Adapter；
- 双方共同跑同一组契约样例；
- 不依赖真实数据库。

### Phase B：Shadow Usage

- 额度侧提供 Durable Reporter；
- Runtime 上报所有 Invocation；
- 额度侧保存 UsageEvent 和 Shadow Credits；
- 不拦截用户请求；
- 按 Provider 后台数据抽样对账。

### Phase C：Gateway Reservation

- Gateway 在 `BackendGateway._submit_turn_locked()` 中完成 Profile 解析和额度 Reserve；
- Turn、Outbox、Reservation 必须同事务；
- `reservation_id` 进入 `TurnTask` 和 Redis Codec；
- Provider 调用前已有完整 Attribution。

注意：当前 `MySQLGatewayRepository.create_turn()` 已经把 Turn 和 `turn.dispatch` Outbox 放在同一事务中。额度协作者应在该事务边界扩展 Reservation，不能先 Reserve、后在另一个无关事务写 Turn。

### Phase D：Settlement 和阻断

- Reporter 写 UsageEvent 后幂等 Settlement；
- 实际低于 Reservation 时 Release；
- 实际高于 Reservation 时按明确政策处理；
- 额度不足时在 Provider 调用前阻断；
- Provider 额度错误和用户额度错误保持不同错误码。

## 18. 仍需额度协作者确认的业务政策

这些问题不阻止模型侧完成 M01 至 M08，但会阻止实际 Credits 结算上线：

1. Student、Teacher、Developer 分别归属个人、Workspace 还是系统成本中心；
2. Worker、Compact、Memory、Vision、Evaluation 是否全部计入发起用户；
3. Provider 无 Usage 时使用 estimated、pending 还是系统承担；
4. Provider Retry/Fallback 的失败消耗是否全部向用户结算；
5. Structured Output 解析失败但 Provider 已消耗 Token 时由谁承担；
6. 流式中断的部分 Usage 如何处理；
7. Reasoning 有独立价格时，如何从 output 中拆分，避免重复收费；
8. 额度不足是否允许自动切换低成本 Profile；
9. 日/月周期使用 UTC、Workspace 时区还是用户时区；
10. `user_id="system"` 哨兵值映射到哪个系统成本中心，以及对应数据库约束；
11. Reporter 持久化采用同步事务、Outbox 还是可靠队列；
12. 同一 Turn 因 Worker Lease 恢复而真实重复调用 Provider 时，重复成本由用户还是系统承担。

## 19. 代码审查清单

审查者逐项回答“是”后才能合并：

- [ ] 所有生产 Provider 请求都经过 `ResilientChatModel`；
- [ ] Reporter 只有一个调用层；
- [ ] 每次 Retry/Fallback 有独立 operation ID；
- [ ] 普通 `ainvoke()` 没有重复上报；
- [ ] Streaming 没有累计值重复相加；
- [ ] Structured Output 能获得 raw Usage；
- [ ] Cancelled 和 Interrupted 都会上报；
- [ ] Reporter 失败不会继续消耗更多 Provider 资源；
- [ ] Provider Response ID 不是 LangChain Run ID；
- [ ] cached 与 cache write 是互不重叠的 input 子集；
- [ ] reasoning 是 output 子集；
- [ ] Canonical total 等于 input + output；
- [ ] Model Profile 和实际 Provider 模型没有混用；
- [ ] 缓存模型实例没有绑定用户信息；
- [ ] Worker Attribution 沿用父 user/workspace/turn；
- [ ] Compact、Memory、Vision、Evaluation purpose 正确；
- [ ] Observability 没有被当作扣费账本；
- [ ] 模型侧没有新增价格、余额或额度表；
- [ ] 所有新增测试不访问真实 Provider；
- [ ] 文档路径和实际实现一致。

## 20. 完成定义

模型管理侧只有满足以下全部条件才算完成：

1. DeepSeek 和 Qwen 的每次实际请求都能生成 `ModelInvocation`；
2. 每次请求都能得到实际 `ModelIdentity`；
3. Provider Usage 能稳定生成 `CanonicalTokenUsage`；
4. Retry、Fallback、Streaming、Structured Output、Worker、Compact、Memory 和 Vision 全部有测试；
5. 成功、失败、中断和取消都只有一次最终 Reporter 调用；
6. Reporter 幂等规则有契约测试；
7. 模型侧不包含任何 Credits 和余额逻辑；
8. 额度协作者可以只实现 `ModelUsageReporter` 接口开始 Shadow 阶段；
9. 本文第 15 节相关测试全部通过，或明确记录无法执行的环境原因；
10. 代码审查清单全部通过。

达到以上条件后，才把本文和公开契约交给额度协作者开始 Durable Usage、Reservation 和 Settlement 实现。
