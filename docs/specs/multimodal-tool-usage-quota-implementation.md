# Pro_NLP 识图、联网搜索与链接读取额度计量实施规格

> 文档类型：可直接委托开发的实施规格
>
> 适用范围：`nlp-agent` 当前工作树
>
> 目标读者：后端开发、数据库开发、前端开发、测试与代码审查人员
>
> 状态：待实施
>
> 编写日期：2026-09-02

## 1. 文档目的

当前项目已经完成文本型的 Token 用量归一化、模型价格版本、Turn 级额度预占、Attempt 级实际结算、Ledger、Bucket 和 Provider 模账单对账。

本次开发不是重做额度系统，而是在现有额度主链路上补齐三类能力：

- 视觉模型识图；
- Provider 原生联网搜索；
- 显式链接读取和网页正文抽取。

实施后的统一链路必须是：

```text
不同能力产生各自的原始用量
    -> 转换为互不重叠的标准计量项 Meter
    -> 根据版本化价格规则换算为 μcredits
    -> 请求发出前预占或追加预占
    -> 请求结束后按实际用量结算
    -> 统一扣减用户、工作区、课堂的 Bucket
    -> 写入不可变 Ledger
    -> 最后使用 Provider 账单进行对账
```

核心原则：

> 统一的是 μcredits、Reservation、Bucket 和 Ledger；不同能力不强制使用同一种计量单位。

## 2. 必须达成的结果

本次实施完成后，系统必须满足：

1. VLM 图片输入可以显示并审计 `image_input_tokens`，但在图片 Token 已包含于总输入 Token 时不得重复扣费。
2. Qwen 原生搜索必须同时计算模型 Token 费用和真实搜索调用费用。
3. `web_fetch` 必须生成独立能力用量事件；是否收费由价格规则决定，不能把工具成本伪装成模型 Token。
4. 收费能力执行前必须检查额度并追加预占，额度不足时不能先调用 Provider 再标记超额。
5. 模型、识图、搜索、链接读取产生的费用全部归属到同一个用户、工作区、课堂和 Turn。
6. 所有实际调用都必须按 `operation_id` 幂等；重试产生新的实际用量事件，事件重放不能重复扣费。
7. Provider 未返回可靠用量时必须进入 `pending` 或 `unavailable`，不能按零费用处理。
8. Provider 账单只能用于对账和追加差额修正，不能覆盖原始 UsageEvent 或 Ledger。
9. 用户用量页面能够按 `model`、`vision`、`search`、`web_fetch` 分类展示 μcredits。
10. 全量启用额度拦截前必须完成 Shadow 和灰度验证。

## 3. 不在本次范围内

以下内容不属于本次开发：

- 修改用户日额度、周额度、Grant、Adjustment 的业务定义；
- 改变角色、用户、工作区和课堂的策略优先级；
- 将 μcredits 改成货币余额；
- 直接同步或复制 Provider 的套餐余额作为用户余额；
- 接入新的搜索 Provider；
- 为图片生成、视频、语音建立完整计费链路；
- 使用 Provider 账单替代请求前额度判断；
- 删除或重算已经完成的历史 Ledger。

本设计应当为未来图片生成、Embedding、Rerank、语音、视频和沙箱算力保留扩展能力，但不得在本次顺带实现。

## 4. 当前项目基线

### 4.1 已存在的额度能力

| 能力 | 当前实现 |
|---|---|
| 模型身份和价格查找键 | `core/model_runtime/usage.py::ModelIdentity.pricing_key` |
| Token 标准化 | `core/model_runtime/normalization.py` |
| 模型 Attempt 上报 | `core/model_runtime/usage.py::ModelUsageReporter` |
| Durable Reporter | `server/quota/reporting.py::DurableModelUsageReporter` |
| Token 价格公式 | `server/quota/pricing.py::PricingCatalog` |
| 请求前预占 | `server/quota/service.py::QuotaService.admit_turn` |
| Attempt 实际结算 | `server/quota/service.py::QuotaService.settle_usage_in_transaction` |
| Turn 剩余预占释放 | `server/quota/service.py::QuotaService.finish_turn` |
| 用量事实表 | `nlp_usage_events` |
| 额度账本 | `nlp_quota_ledger_entries` |
| Provider 对账 | `nlp_quota_provider_billing`、`server/quota/operations.py` |
| 日聚合 | `nlp_quota_daily_rollups` |

### 4.2 已存在的识图链路

视觉模型通过：

```text
image_analyze
    -> ImageAnalyzeService
    -> ModelRuntimeVLMProvider
    -> model route: vision-worker
    -> preset: vision-qwen-plus
    -> model: qwen3-vl-plus
    -> pricing_key: qwen/qwen3-vl-plus
```

`server/tools/vision/vlm.py` 已通过 `bind_usage_purpose("vision")` 标记用途。因此 VLM 的总输入和输出 Token 已经可以进入现有模型 Usage Reporter。

当前缺口：

- `image_tokens` 在 Provider 原始响应中可能存在，但进入 `CanonicalTokenUsage` 后没有独立字段；
- `nlp_usage_events` 无法保存文本输入与图片输入的细分；
- Turn Admission 只按 Coordinator 估算，没有在 VLM 调用前追加预占；
- RapidOCR 是本地能力，目前没有独立用量事件。

### 4.3 已存在的联网搜索链路

当前配置存在专用 Preset：

```yaml
worker-qwen-web:
  model: qwen3.7-plus
  native_search:
    enabled: true
    forced: true
    strategy: turbo
```

`core/model_runtime/adapters/qwen.py` 会发送：

```json
{
  "enable_search": true,
  "search_options": {
    "forced_search": true,
    "search_strategy": "turbo"
  }
}
```

当前缺口：

- 搜索带来的模型输入 Token 已包含在模型 Usage 中；
- Provider 可能返回的 `usage.plugins.search.count` 没有进入 Canonical Usage；
- 搜索按调用次数产生的费用没有结算；
- 原始搜索计量明细没有持久化，无法稳定对账。

### 4.4 已存在的链接读取链路

`server/tools/api/web_fetch_tool.py` 调用 `server/tools/web/fetch.py::WebFetchService`，由项目本地使用 `httpx` 下载和抽取网页。

当前缺口：

- 没有用量 Reporter；
- 没有独立的 `operation_id`；
- 缓存命中、实际网络请求、返回字节数和抽取字符数没有进入额度系统；
- 无法配置“免费、按次、按流量”的产品计费政策。

### 4.5 当前额度开关

当前配置为：

```yaml
gateway:
  quota_enforcement: false
```

实施期间必须保持默认关闭。完成 Shadow、迁移、价格配置和回归后才能灰度启用。

## 5. 术语和边界

### 5.1 Usage Fact

一次真实执行产生的不可变事实。例如：

- 一个模型 Provider Attempt；
- 一次真实联网搜索；
- 一次未命中缓存的 HTTP 下载；
- 一次本地 OCR 处理。

### 5.2 Meter

Meter 是可计价的标准计量项。首期支持：

```text
model.input.ordinary_tokens
model.input.cached_tokens
model.input.cache_write_tokens
model.output.tokens
model.output.reasoning_tokens
model.input.image_tokens
search.requests
web_fetch.requests
web_fetch.bytes
ocr.pages
```

其中 `model.input.image_tokens` 默认只用于明细和审计。只有价格规则明确将图片 Token 与普通输入 Token 分开计价时，才可以单独结算。

### 5.3 Usage Source

统一支持：

```text
provider    Provider 明确返回
measured    项目在真实执行边界测得
estimated   本地估算
none        没有可靠用量
```

现有模型契约暂时只支持 `provider / estimated / none`。新增能力契约可以支持 `measured`，但不得破坏现有模型接口。

### 5.4 Usage Status

```text
exact        Provider 返回或本地执行边界精确测量
estimated    只能使用本地估算
pending      等待 Provider 或账单补全
unavailable  无法取得可靠用量
```

### 5.5 operation_id

- 每一次真实执行必须有独立 `operation_id`；
- 相同事件重放沿用相同 `operation_id`；
- Retry 或 Fallback 是新的真实执行，必须使用新的 `operation_id`；
- `operation_id` 是结算幂等键，不得使用 URL、文件名或用户输入替代；
- Provider Response ID 只作为辅助对账键。

## 6. 计价公式

### 6.1 现有模型公式保持不变

```text
ordinary_input_tokens =
    input_tokens
    - cached_input_tokens
    - cache_write_input_tokens

ordinary_output_tokens =
    output_tokens - reasoning_output_tokens
```

当价格规则配置独立 Reasoning 价格时：

```text
model_credits_micro = ceil((
    ordinary_input_tokens × ordinary_input_rate
  + cached_input_tokens × cached_input_rate
  + cache_write_input_tokens × cache_write_rate
  + ordinary_output_tokens × output_rate
  + reasoning_output_tokens × reasoning_rate
) / 1,000,000)
```

当 Reasoning 没有独立价格时，`output_tokens` 整体按输出价格计算，不能再次累加 Reasoning Token。

### 6.2 通用 Meter 公式

每一条非模型计量项使用整数运算：

```text
line_credits_micro = ceil(quantity × rate_micro / rate_unit)
```

一次能力事件的总额度：

```text
capability_credits_micro = max(
    minimum_charge_micro,
    Σ line_credits_micro
)
```

一次 Turn 的总额度：

```text
turn_credits_micro =
    Σ model_attempt_credits_micro
    + Σ capability_event_credits_micro
```

要求：

- 全程使用整数，禁止使用 `float`；
- 每条 Meter 独立向上取整，避免不同单位之间互相抵消小数；
- 价格版本以事件发生时间选择；
- UsageEvent 必须固化实际使用的 `pricing_key` 和 `pricing_version`；
- 历史事件不得因新价格发布而重算。

### 6.3 防止重复扣费

必须遵守以下关系：

```text
text_input_tokens + image_input_tokens = input_tokens
```

如果图片与文本使用同一个输入价格：

```text
只对 input_tokens 计价
image_input_tokens 仅保存为明细
```

如果图片有独立价格：

```text
text_ordinary_input_tokens =
    input_tokens
    - image_input_tokens
    - cached_input_tokens
    - cache_write_input_tokens

分别对 text_ordinary_input_tokens 和 image_input_tokens 计价
```

在 Provider 没有明确说明缓存 Token 是否与图片 Token 重叠时，不得自行执行上述拆分。应保存 Provider 的原始 billing detail，并让该事件进入 `pending`，直到价格规则明确。

## 7. 各能力的正式结算政策

### 7.1 VLM 识图

VLM 使用现有模型价格规则，不额外收取“识图固定费”。

结算项：

```text
输入 Token，包含文本和图片 Token
缓存读取和写入 Token（Provider 返回时）
输出 Token
Reasoning Token（Provider 返回且价格独立时）
```

必须新增并保存：

```text
text_input_tokens
image_input_tokens
```

请求前估算：

- 文本部分使用现有消息估算；
- 图片部分由 Vision Adapter 根据模型版本、缩放后的宽高和 Provider 公式估算；
- 估算值只用于预占；
- 最终必须使用 Provider 返回的实际 `input_tokens` 和 `image_tokens` 结算。

### 7.2 本地 RapidOCR

RapidOCR 当前没有外部 Provider 账单，首期默认价格应配置为 0，而不是完全不记录。

推荐 Meter：

```text
ocr.pages
```

记录事件的价值：

- 后续可以分析本地计算资源；
- 将来切换收费 OCR Provider 时无需修改主链路；
- 可以区分纯 OCR 与 VLM Fusion。

是否扣费完全由 `internal/rapidocr/v1` 价格规则决定。

### 7.3 Qwen 原生搜索

一次原生搜索模型调用产生两类费用：

```text
模型 Token 费用
+
搜索策略调用费用
```

模型 Token 继续由 `DurableModelUsageReporter` 结算。

搜索调用次数从 Provider 原始 Usage 中提取：

```text
usage.plugins.search.count
usage.plugins.web_search.count
```

首期兼容两种键名，最终归一化为：

```text
meter = search.requests
quantity = count
```

价格键必须包含足以唯一确定价格的维度，例如：

```text
qwen/cn-beijing/web-search/turbo
qwen/cn-beijing/web-search/max
qwen/singapore/web-search/turbo
```

不得只使用 `qwen/web-search`，因为地区和策略可能有不同价格。

如果 Preset 开启搜索但 Provider 没返回真实次数：

- `forced_search=true`：记录 `estimated` 或 `pending`，按配置选择保守预估 1 次；
- `forced_search=false`：不得因为开启了能力就直接扣 1 次，应记录 `pending` 并等待对账；
- 不得通过输入 Token 明显增大来推断并作为 exact 用量。

### 7.4 显式链接读取 `web_fetch`

当前 `web_fetch` 是本地 HTTP 请求，不等同于 Provider 原生 Web Extractor。

首期需要支持以下 Meter：

```text
web_fetch.requests
web_fetch.bytes
```

推荐默认产品规则：

```text
缓存命中：记录 exact 用量，价格为 0
成功且未命中缓存：按 1 次请求计量
下载字节：保存明细，首期价格为 0
URL 校验或 SSRF 拒绝：不生成收费事件
真实网络请求后失败：生成事件，是否收费由规则决定
```

链接正文在后续模型调用中会成为模型输入 Token，该部分继续由模型 Usage 结算。不得再把 `extracted_chars` 换算成模型 Token 重复扣费。

如果未来改用收费 Web Extractor，应使用新的 Provider Pricing Key，不得修改历史 `internal/web-fetch/v1` 事件。

## 8. 新增公开契约

新增模块：

```text
core/usage_metering/contracts.py
core/usage_metering/context.py
core/usage_metering/reporters.py
```

建议契约如下，字段名称可以微调，但语义不可改变：

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MeteredUsageItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    meter: str = Field(min_length=1, max_length=128)
    quantity: int = Field(strict=True, ge=0)
    unit: str = Field(min_length=1, max_length=32)


class CapabilityUsageEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    parent_operation_id: str | None = None
    reservation_id: str | None = None
    request_id: str
    user_id: str
    workspace_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    worker_id: str | None = None
    purpose: str
    capability_type: Literal["search", "web_fetch", "ocr"]
    provider: str
    pricing_key: str
    provider_response_id: str | None = None
    usage_source: Literal["provider", "measured", "estimated", "none"]
    usage_status: Literal["exact", "estimated", "pending", "unavailable"]
    items: tuple[MeteredUsageItem, ...]
    occurred_at: datetime
    raw_usage: dict
```

Reporter：

```python
class CapabilityUsageReporter(Protocol):
    async def report(self, event: CapabilityUsageEvent) -> None:
        ...
```

要求：

- 归属信息复用现有 `UsageAttributionContext`；
- 不在工具参数中信任客户端传入的 `user_id`、`workspace_id` 或 `reservation_id`；
- Reporter 在应用和独立 Worker 进程启动时注入；
- 生产环境启用收费能力时，Reporter 未配置必须启动失败或健康检查失败；
- Reporter 失败不得静默丢弃费用。

Capability 的 `operation_id` 是最大 128 字符的稳定不透明字符串，不要求使用 UUIDv4。模型 Attempt 继续保持现有 UUIDv4 约束。原生搜索事件建议使用：

```text
{model_operation_id}:native-search
```

这样模型事件重放时可以稳定定位同一搜索事件。

额度层的 `UsageSource` 和 `UsageRecordResult` 需要增加 `measured`，使本地 HTTP、缓存和 OCR 的精确测量不会被伪装成 `provider`。`CanonicalTokenUsage` 的模型侧 `UsageSource` 保持现状，避免破坏稳定模型接口。

## 9. 模型 Usage 契约扩展

在 `CanonicalTokenUsage` 中新增：

```python
text_input_tokens: StrictNonNegativeInt | None = None
image_input_tokens: StrictNonNegativeInt = 0
provider_usage_details: dict[str, object] = Field(default_factory=dict)
```

兼容规则：

- 老 Provider 不返回明细时，`text_input_tokens=None`，不能伪造为全部文本；
- `image_input_tokens <= input_tokens`；
- `text_input_tokens` 非空时，必须满足 `text_input_tokens + image_input_tokens <= input_tokens`；
- `provider_usage_details` 只保存白名单计量字段，不保存提示词、网页正文或图片内容；
- 搜索插件计数可以保留在 `provider_usage_details`，但搜索结算必须生成独立 Capability Usage Event。

需要从以下 Provider 字段兼容读取：

```text
input_token_details.image_tokens
prompt_tokens_details.image_tokens
input_tokens_details.image_tokens
input_token_details.text_tokens
prompt_tokens_details.text_tokens
input_tokens_details.text_tokens
usage.plugins.search.count
usage.plugins.web_search.count
```

## 10. 数据库设计

### 10.1 迁移要求

新增一条 Alembic 迁移。创建前先运行 `alembic heads` 确定真实 Head；不得仅根据文件名猜测 `down_revision`。

建议文件名：

```text
migrations/versions/20260902_45_capability_usage_metering.py
```

如果执行时已有新的 Head，应调整编号和 `down_revision`，不得制造并行 Head，除非仓库维护者明确要求 merge migration。

### 10.2 扩展 `nlp_usage_events`

新增可空或带默认值字段：

```text
text_input_tokens       BIGINT UNSIGNED NULL
image_input_tokens      BIGINT UNSIGNED NOT NULL DEFAULT 0
usage_details_json      JSON NOT NULL
```

迁移不得把历史 `input_tokens` 全部回填为 `text_input_tokens`，因为历史事件可能包含无法识别的多模态输入。历史记录保持：

```text
text_input_tokens = NULL
image_input_tokens = 0
usage_details_json = {}
```

### 10.3 新表 `nlp_meter_pricing_rules`

字段：

```text
id                    CHAR(36) PK
pricing_key           VARCHAR(255) NOT NULL
version               VARCHAR(64) NOT NULL
meter                 VARCHAR(128) NOT NULL
unit                  VARCHAR(32) NOT NULL
rate_unit             BIGINT UNSIGNED NOT NULL
rate_micro            BIGINT UNSIGNED NOT NULL
minimum_charge_micro  BIGINT UNSIGNED NOT NULL DEFAULT 0
effective_from        DATETIME(6) NOT NULL
effective_until       DATETIME(6) NULL
status                VARCHAR(16) NOT NULL
created_by            VARCHAR(128) NOT NULL
created_at            DATETIME(6) NOT NULL
```

约束与索引：

```text
UNIQUE(pricing_key, version, meter)
CHECK(rate_unit > 0)
CHECK(effective_until IS NULL OR effective_until > effective_from)
INDEX(pricing_key, effective_from, effective_until)
INDEX(status, effective_from)
```

同一 `pricing_key + meter` 的生效区间不得重叠。应用层和数据库事务都要检查。

### 10.4 新表 `nlp_capability_usage_events`

字段：

```text
id                    CHAR(36) PK
operation_id          VARCHAR(128) NOT NULL
parent_operation_id   VARCHAR(128) NULL
reservation_id        VARCHAR(128) NULL
request_id            VARCHAR(128) NOT NULL
user_id               VARCHAR(128) NOT NULL
workspace_id          VARCHAR(128) NULL
conversation_id       VARCHAR(128) NULL
turn_id               VARCHAR(128) NULL
worker_id             VARCHAR(128) NULL
purpose               VARCHAR(32) NOT NULL
capability_type       VARCHAR(32) NOT NULL
provider              VARCHAR(128) NOT NULL
provider_response_id  VARCHAR(255) NULL
pricing_key           VARCHAR(255) NOT NULL
pricing_version       VARCHAR(64) NULL
usage_source          VARCHAR(16) NOT NULL
usage_status          VARCHAR(16) NOT NULL
credits_micro         BIGINT UNSIGNED NULL
raw_usage_json        JSON NOT NULL
dedupe_key            VARCHAR(255) NOT NULL
idempotency_key       VARCHAR(255) NOT NULL
occurred_at           DATETIME(6) NOT NULL
created_at            DATETIME(6) NOT NULL
archived_at           DATETIME(6) NULL
archive_batch_id      CHAR(36) NULL
```

约束与索引：

```text
UNIQUE(operation_id)
UNIQUE(idempotency_key)
INDEX(user_id, occurred_at)
INDEX(workspace_id, occurred_at)
INDEX(capability_type, occurred_at)
INDEX(provider, occurred_at)
INDEX(usage_status, occurred_at)
INDEX(reservation_id, occurred_at)
```

### 10.5 新表 `nlp_capability_usage_items`

字段：

```text
id                CHAR(36) PK
event_id          CHAR(36) NOT NULL FK -> nlp_capability_usage_events.id
meter             VARCHAR(128) NOT NULL
quantity          BIGINT UNSIGNED NOT NULL
unit              VARCHAR(32) NOT NULL
rate_unit         BIGINT UNSIGNED NULL
rate_micro        BIGINT UNSIGNED NULL
line_credits_micro BIGINT UNSIGNED NULL
created_at        DATETIME(6) NOT NULL
```

约束与索引：

```text
UNIQUE(event_id, meter)
INDEX(meter, created_at)
```

事件结算完成后，Item 必须固化当时使用的 `rate_unit`、`rate_micro` 和 `line_credits_micro`，保证历史可复现。

### 10.6 Provider Billing 扩展

扩展 `nlp_quota_provider_billing`：

```text
usage_event_type          VARCHAR(16) NOT NULL DEFAULT 'model'
matched_capability_event_id CHAR(36) NULL
billed_usage_json         JSON NOT NULL
```

`usage_event_type` 支持：

```text
model
capability
```

匹配规则：

1. 优先使用 `provider + operation_id`；
2. 其次使用 `provider + provider_response_id`；
3. 无法唯一匹配时保持 `unmatched`；
4. 禁止仅按用户和时间窗口自动确定唯一事件。

## 11. 动态追加预占

### 11.1 当前问题

当前 Admission 只对 Turn 的 Coordinator 调用进行一次估算。后续 Worker、Retry、Fallback、Vision 和搜索可能继续消费同一个 Reservation，造成实际费用超过请求前预占。

### 11.2 新增服务接口

在 `QuotaService` 新增：

```python
def reserve_additional(
    self,
    *,
    reservation_id: str,
    operation_key: str,
    estimated_micro: int,
    reason: str,
    pricing_key: str | None,
    now: datetime | None = None,
) -> AdditionalReservationResult:
    ...
```

事务要求：

1. `SELECT ... FOR UPDATE` 锁定 Reservation；
2. 使用 `operation_key` 检查追加预占 Ledger 是否已经存在；
3. 锁定该 Reservation 关联的全部日、周 Bucket；
4. 重新计算用户、工作区和课堂的可用额度；
5. 校验累计请求费用不超过所有有效 `request_limit_micro`；
6. 给每个 Bucket 原子增加 `reserved_micro`；
7. 给 Reservation 增加 `reserved_micro`；
8. 写入 `entry_type=reserve_increment` 的 Ledger；
9. 同一 `operation_key` 重放返回原结果，不重复预占；
10. 任一主体额度不足时整个事务回滚，Provider 不得被调用。

Ledger 幂等键建议：

```text
reserve-increment:{reservation_id}:{operation_key}:{bucket_id}
```

未消耗的追加预占可以保留到 `finish_turn` 统一释放。首期不强制实现单项提前释放，避免并发 Attempt 错误释放其他调用仍需要的额度。

### 11.3 调用位置

以下边界必须追加预占：

| 边界 | 预占内容 |
|---|---|
| 每个真实模型 Provider Attempt 发出前 | 当前模型输入估算 + 当前 Preset 最大输出风险 |
| Vision VLM 发出前 | 图片 Token 估算 + 文本输入估算 +最大输出风险 |
| 原生搜索模型 Attempt 发出前 | 模型风险 + 最大搜索次数风险 |
| 收费 `web_fetch` 网络请求发出前 | 一次请求基础费用；字节费用可使用最大响应上限估算 |
| 收费 OCR 执行前 | 页数或图片数估算 |

必须避免 Coordinator 首次 Admission 和 Runtime Attempt 再次完整预占同一笔风险。建议采用“确保预占下限”而不是无条件累加：

```text
required_reserve = 当前已经开始但尚未结算的调用风险总和
additional = max(0, required_reserve - reservation.reserved_micro)
```

如果实现者无法在第一阶段可靠维护“在途调用风险总和”，允许采用更保守的每 Attempt 追加预占，但必须通过测试证明 Turn 结束后剩余预占会完整释放，并在产品文档中说明短时间内可能锁定更多余额。

## 12. Reporter 与事务

新增 `DurableCapabilityUsageReporter`，建议位置：

```text
server/quota/capability_reporting.py
```

处理顺序：

```text
接收 CapabilityUsageEvent
    -> 检查 operation_id
    -> 选择生效的 Meter Pricing Rule
    -> 逐项整数计价
    -> 开启数据库事务
    -> 幂等写 Event 和 Items
    -> 调用 QuotaService.settle_usage_in_transaction
    -> 同事务写 Ledger 和更新 Bucket
    -> 提交
    -> 发送额度快照通知
```

幂等规则：

- 同一 `operation_id`、相同 payload：无操作成功；
- 同一 `operation_id`、不同 payload：抛出冲突错误并告警；
- `pending/unavailable -> exact/estimated`：允许受控补全并执行差额结算；
- `exact -> exact` 且事实不同：禁止覆盖；
- Event、Items、Ledger 和 Bucket 更新必须位于同一事务。

Reporter 不得自己实现另一套 Bucket 扣减逻辑，必须复用 `QuotaService.settle_usage_in_transaction`。

## 13. 各适配器改动

### 13.1 Qwen Adapter

修改：

```text
core/model_runtime/adapters/qwen.py
core/model_runtime/normalization.py
core/model_runtime/usage.py
```

要求：

- 从原始 Usage 提取文本、图片 Token 明细；
- 从原始 Usage 提取搜索插件调用次数；
- 保留必要的 Provider 计量明细；
- 不保留 Prompt、搜索正文或图片数据；
- 非流式和流式最终 Usage 行为一致；
- 累计流不得把 `plugins.search.count=1` 按每个 Chunk 重复累加；
- 模型 Reporter 成功后，为搜索部分生成一个关联的 Capability Event；
- 搜索事件的 `parent_operation_id` 指向模型 `operation_id`。

搜索事件幂等键建议：

```text
native-search:{model_operation_id}
```

### 13.2 Vision Adapter

修改：

```text
server/tools/vision/vlm.py
server/tools/vision/service.py
server/tools/vision/ocr.py
```

要求：

- 在 VLM Provider 请求前计算保守图片 Token 估算并追加预占；
- 最终模型费用仍由模型 Reporter 结算；
- 不在 Vision Service 再结算一次 VLM Token；
- OCR 生成独立 Capability Event；
- Fusion 路由允许同时产生 OCR 事件和 VLM 模型事件；
- 图片二进制、Base64 和 OCR 全文不得进入额度事件的 `raw_usage_json`。

### 13.3 Web Fetch

修改：

```text
server/tools/api/web_fetch_tool.py
server/tools/web/fetch.py
server/tools/web/contracts.py
server/tools/web/cache.py
```

新增内部 `WebFetchMeasurement`，不要把计量控制字段直接加入返回给模型的正文结构：

```text
cache_hit
network_attempts
response_bytes
extracted_chars
```

`WebFetchService` 可以返回内部执行封装，或者通过 Reporter 在下载边界直接上报。无论采用哪种方式，`web_fetch` 工具返回给模型的 JSON 都不得包含 Reservation、Ledger、价格规则和内部操作主键。

要求：

- 每次实际网络 Attempt 生成独立 `operation_id`；
- 缓存命中生成一条零价 measured 事件，便于统计；
- 调用前根据配置价格追加预占；
- 实际请求完成后按真实请求次数和字节数结算；
- Tool Runtime 外层重试会重新调用 Wrapper，因此新的真实网络请求必须产生新的事件；
- 缓存 Key、URL Host 摘要可以进入 metadata，完整网页正文不得进入额度表；
- URL 可以按现有安全规则保存规范化值或哈希，不能绕过当前 SSRF 防护。

## 14. Pricing 管理 API

新增开发者 API，沿用现有 Pricing Rule 权限边界：

```text
GET    /api/developer/quota/meter-pricing-rules
POST   /api/developer/quota/meter-pricing-rules
POST   /api/developer/quota/meter-pricing-rules/{id}/retire
```

请求体：

```json
{
  "pricing_key": "qwen/cn-beijing/web-search/turbo",
  "version": "2026-02-27",
  "meter": "search.requests",
  "unit": "call",
  "rate_unit": 1000,
  "rate_micro": 3000000,
  "minimum_charge_micro": 0,
  "effective_from": "2026-02-27T00:00:00Z",
  "effective_until": null
}
```

上例数字仅用于展示字段含义，不是本项目正式定价。正式 μcredits 比率必须由产品负责人配置。

API 必须：

- 严格整数校验；
- 拒绝重叠生效区间；
- 发布后不可原地修改价格；
- 只能 Retire，再创建新版本；
- 记录操作人；
- 时间统一为 UTC。

## 15. 用量查询与前端

### 15.1 Read Service

扩展 `server/quota/usage.py`，将模型 UsageEvent 和 Capability UsageEvent 聚合为统一返回：

```json
{
  "credits_micro": 12345,
  "credits_complete": true,
  "breakdown": [
    {
      "category": "model",
      "provider": "qwen",
      "credits_micro": 9000
    },
    {
      "category": "vision",
      "provider": "qwen",
      "credits_micro": 2500
    },
    {
      "category": "search",
      "provider": "qwen",
      "credits_micro": 500
    },
    {
      "category": "web_fetch",
      "provider": "internal",
      "credits_micro": 345
    }
  ]
}
```

注意：`purpose=vision` 的模型事件在展示时归到 Vision 分类，但它仍然是 Model UsageEvent，不应复制为第二条 Capability Event。

### 15.2 Daily Rollup

可选择：

1. 扩展现有 `nlp_quota_daily_rollups` 增加 `category` 和 Meter JSON；或
2. 新建 `nlp_quota_capability_daily_rollups`。

首期推荐新建独立 Capability Rollup，避免修改现有唯一约束导致历史聚合迁移复杂。查询层负责合并两类 Rollup。

### 15.3 页面改动

修改：

```text
webui/src/modules/quota/QuotaUsagePage.tsx
webui/src/modules/quota/QuotaManagementPage.tsx
```

用户用量页增加：

- 模型文本；
- 视觉模型；
- 联网搜索；
- 链接读取；
- 待结算费用；
- 各分类的 μcredits 和调用次数。

开发者管理页增加：

- Meter Pricing Rule 列表；
- 新建价格版本；
- Retire 操作；
- Provider Billing 的事件类型和匹配对象；
- 对账差异的 Meter 明细。

前端不得自行计算 Credits，只显示后端固化结果。

## 16. Provider 账单对账

模型、搜索和网页抽取必须继续采用“内部计量 + 平台对账”，二者不是替代关系。

对账结果：

```text
matched       本地与 Provider 一致
mismatch      找到事件但计量或金额不同
unmatched     找不到唯一事件
pending       本地或 Provider 数据不完整
```

差异修复：

- 保留原始 Provider Statement；
- 保留原始模型或能力 Usage Event；
- 新增一条 `reconciliation_adjustment` Ledger；
- 调整量可以为正或负；
- 必须包含操作人、原因和修复幂等键；
- 不允许直接更新历史 `credits_micro` 来掩盖差异。

平台赠送额度、免费额度、资源包抵扣和折扣属于 Provider 账户层，不直接改变用户内部用量事实。是否把优惠传递给用户属于产品政策，不能在对账逻辑中隐式处理。

## 17. 异常与失败政策

| 场景 | 行为 |
|---|---|
| 价格规则缺失 | 请求前拒绝收费能力；Shadow 阶段记录告警 |
| Provider 返回 Token、未返回搜索次数 | 模型 Token exact；搜索 pending/estimated |
| Provider 返回搜索次数、未返回 Token | 搜索 exact；模型 unavailable/pending |
| Reporter 数据库失败 | 当前调用整体失败，不得静默继续 |
| 同一 operation_id 不同事实 | 冲突并告警，不覆盖历史 |
| 实际费用大于预占 | 正常结算、标记 over_limit、告警；后续调用禁止继续扩额 |
| 实际费用小于预占 | 结算实际费用，Turn 完成释放剩余预占 |
| Tool 在 Provider 调用前失败 | 不产生实际费用，追加预占在 Turn 完成释放 |
| Tool 超时但 Provider 可能已处理 | 记录 pending，等待响应或账单对账 |
| Turn 取消 | 已产生的实际费用继续结算，未用预占释放 |
| Cache 命中 | 记录 measured 零价事件，不产生网络请求费用 |

## 18. 安全与隐私要求

额度事件和账单表不得保存：

- 完整 Prompt；
- 模型输出正文；
- 网页正文；
- 图片 Base64 或二进制；
- Cookie、Authorization Header、API Key；
- 用户上传文件的绝对路径；
- Provider 原始响应中的敏感内容。

允许保存：

- Token 数；
- Meter、数量和单位；
- Provider Response ID；
- URL Host 的不可逆摘要；
- 图片宽高、缩放宽高、页数和字节数；
- 搜索策略和调用次数；
- 状态、错误类型和时间。

所有归属字段必须来自服务端已认证 Context，不能从模型工具参数接受。

## 19. 实施任务拆分

### Task 0：基线确认

目标：确认开始开发前仓库和数据库迁移状态。

工作：

- 记录 `git status --short`；
- 运行 `alembic heads`；
- 跑现有 quota、model usage、vision、web fetch 专项测试；
- 不修改用户已有的 `docs/image-understanding.md` 工作树改动。

交付：基线测试结果和真实 Alembic Head。

### Task 1：模型多模态 Usage 契约

依赖：Task 0。

主要文件：

```text
core/model_runtime/usage.py
core/model_runtime/normalization.py
core/model_runtime/adapters/qwen.py
tests/test_model_usage_contracts.py
tests/test_model_usage_providers.py
```

交付：图片 Token 和搜索计量明细能够从非流式、流式 Provider 响应稳定归一化。

### Task 2：Meter 契约和计价引擎

依赖：Task 0。

主要文件：

```text
core/usage_metering/*
server/quota/meter_pricing.py
tests/test_quota_meter_pricing.py
```

交付：严格整数计价、版本选择、区间冲突、未知价格和舍入测试。

### Task 3：数据库迁移和 ORM

依赖：Task 1、Task 2 的字段冻结。

主要文件：

```text
migrations/versions/*_capability_usage_metering.py
server/quota/models.py
server/infrastructure/mysql/table_comments.py
tests/test_quota_bootstrap.py
```

交付：模型用量扩展字段、Meter 价格表、能力事件表、明细表和 Provider Billing 扩展。

### Task 4：Durable Capability Reporter

依赖：Task 2、Task 3。

主要文件：

```text
server/quota/capability_reporting.py
server/quota/bootstrap.py
server/quota/service.py
tests/test_capability_usage_reporting.py
```

交付：Event、Item、Ledger、Bucket 同事务，完整幂等和补全行为。

### Task 5：动态追加预占

依赖：Task 3。

主要文件：

```text
server/quota/contracts.py
server/quota/service.py
gateway/turn_execution.py
core/model_runtime/runtime.py
tests/test_quota_enforcement.py
tests/test_quota_mysql_integration.py
```

交付：每个收费调用发出前能够原子追加预占，额度不足时不触发 Provider。

### Task 6：识图和 OCR 接入

依赖：Task 1、Task 4、Task 5。

主要文件：

```text
server/tools/vision/vlm.py
server/tools/vision/service.py
server/tools/vision/ocr.py
tests/test_vision_vlm.py
```

交付：VLM 不重复结算，OCR 产生独立零价或可配置价格事件，Vision 调用前完成预占。

### Task 7：Qwen 原生搜索接入

依赖：Task 1、Task 4、Task 5。

主要文件：

```text
core/model_runtime/adapters/qwen.py
core/model_runtime/runtime.py
server/quota/capability_reporting.py
tests/test_model_runtime.py
tests/test_model_usage_providers.py
```

交付：模型 Token 与搜索次数分别结算且关联，不因流式累计 Usage 重复扣费。

### Task 8：Web Fetch 接入

依赖：Task 4、Task 5。

主要文件：

```text
server/tools/api/web_fetch_tool.py
server/tools/web/fetch.py
server/tools/web/contracts.py
server/tools/web/cache.py
tests/test_web_fetch_tool.py
tests/test_web_contracts.py
tests/test_web_api.py
```

交付：缓存、网络 Attempt、字节数计量和收费策略正确。

### Task 9：查询、对账和前端

依赖：Task 3、Task 4、Task 6、Task 7、Task 8。

主要文件：

```text
server/quota/usage.py
server/quota/operations.py
server/quota/management.py
server/web/contracts.py
server/web/app.py
webui/src/modules/quota/QuotaUsagePage.tsx
webui/src/modules/quota/QuotaManagementPage.tsx
```

交付：分类用量、Meter 价格管理、两类事件对账和前端展示。

### Task 10：Shadow、灰度和运行手册

依赖：所有开发任务。

工作：

- 生产保持 `quota_enforcement=false`；
- 运行 Shadow，比较本地事件和 Provider 用量；
- 配置所有启用能力的有效价格版本；
- 检查 pending/unavailable 比例；
- 按用户或工作区灰度；
- 最后才考虑全量启用。

## 20. 测试矩阵

### 20.1 计价

- [ ] 每种 Meter 使用整数计价；
- [ ] 1 次调用不会因为千次单价而变成免费；
- [ ] 不同 Meter 分别向上取整；
- [ ] 生效区间边界正确；
- [ ] 重叠价格规则被拒绝；
- [ ] 缺失价格不按 0 处理；
- [ ] 已退休历史版本仍可用于历史事件补全。

### 20.2 识图

- [ ] 图片输入总 Token 包含图片 Token；
- [ ] 图片 Token 不会重复计价；
- [ ] VLM 的 `purpose=vision`；
- [ ] VLM 调用前追加预占；
- [ ] OCR-only 只生成 OCR 能力事件；
- [ ] VLM-only 只生成模型事件；
- [ ] Fusion 同时生成 OCR 和模型事件；
- [ ] 图片内容不进入 Usage 表。

### 20.3 搜索

- [ ] `plugins.search.count=1` 生成一条搜索事件；
- [ ] `plugins.web_search.count` 可以兼容；
- [ ] 搜索模型 Token 与搜索调用分别计价；
- [ ] 流式累计 Usage 只结算最终 count；
- [ ] Retry 的实际搜索分别记录；
- [ ] forced 搜索缺少 count 时进入保守策略；
- [ ] 自动搜索缺少 count 时不伪装为 exact 1 次。

### 20.4 Web Fetch

- [ ] URL 安全校验失败不收费；
- [ ] 缓存命中产生零价事件；
- [ ] 未命中缓存按真实网络 Attempt 记录；
- [ ] 返回字节数准确；
- [ ] 外层重试产生新的真实 Attempt；
- [ ] 正文不写入额度事件；
- [ ] 后续模型读取正文的 Token 正常结算且不重复。

### 20.5 Reservation 与 Ledger

- [ ] 追加预占同时作用于用户、工作区和课堂 Bucket；
- [ ] 任一 Bucket 不足时整个追加预占回滚；
- [ ] 重复 operation key 不重复预占；
- [ ] 结算释放正确数量的 reserved；
- [ ] Turn 完成释放全部剩余预占；
- [ ] 实际费用超过预占时标记 over_limit；
- [ ] 取消和中断保留已经发生的费用；
- [ ] Ledger Replay 可以恢复 Bucket。

### 20.6 对账

- [ ] 模型账单匹配模型事件；
- [ ] 搜索账单匹配能力事件；
- [ ] 同一 Provider Response 关联模型和搜索时不会错误合并；
- [ ] 无法唯一匹配时保持 unmatched；
- [ ] 差额修复追加 Ledger，不覆盖 Usage；
- [ ] 修复幂等。

## 21. 验收命令

实现者应使用项目虚拟环境。Windows PowerShell 7 示例：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_model_usage_contracts.py `
  tests/test_model_usage_providers.py `
  tests/test_model_usage_reporting.py `
  tests/test_model_runtime.py `
  tests/test_quota_pricing.py `
  tests/test_quota_enforcement.py `
  tests/test_quota_reporting.py `
  tests/test_quota_management.py `
  tests/test_quota_phase4.py `
  tests/test_quota_bootstrap.py `
  tests/test_vision_vlm.py `
  tests/test_web_fetch_tool.py `
  tests/test_web_contracts.py `
  tests/test_web_api.py -q
```

新增测试文件后加入：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_quota_meter_pricing.py `
  tests/test_capability_usage_reporting.py -q
```

静态检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  core/usage_metering `
  core/model_runtime `
  server/quota `
  server/tools `
  gateway
```

前端：

```powershell
Set-Location webui
npm test -- --run
npm run build
```

如果虚拟环境解释器无法启动，应先记录环境问题，不得改用系统 Python 后直接宣称回归通过。

## 22. 交付物要求

受托开发者必须提交：

1. 完整代码；
2. 单一可回放的 Alembic 迁移链；
3. 新增和更新的自动化测试；
4. Meter 和 Pricing Key 清单；
5. 数据库表和索引说明；
6. Shadow 对比报告；
7. pending、unavailable 和 reconciliation mismatch 的监控指标；
8. 灰度开启和回滚说明；
9. 最终 Commit SHA；
10. 未解决问题清单。

不得只交分支名，也不得只提供截图证明完成。

## 23. Code Review 阻断项

出现以下任一情况不得合并：

- 图片 Token 同时包含在 `input_tokens` 又被额外累加；
- 仅因为 Preset 开启搜索就把每个请求记为 exact 搜索 1 次；
- `web_fetch` 正文写入额度数据库；
- 使用 `float` 计算 μcredits；
- Provider 账单直接覆盖 UsageEvent 或 Ledger；
- 价格规则原地修改导致历史金额不可复现；
- 收费工具在额度检查前已经发出 Provider 请求；
- Web 和独立 Worker 的 Reporter 注入语义不一致；
- Retry 或 Fallback 共用一个 `operation_id`；
- 同一 `operation_id` 不同 payload 被静默覆盖；
- 缺少价格或用量时按零费用处理；
- 只扣用户 Bucket，没有同步约束工作区和课堂 Bucket；
- 全量启用额度拦截但没有 Shadow 和灰度记录。

## 24. 最终验收场景

使用一个同时受用户、工作区和课堂额度限制的测试账户，执行以下场景：

```text
用户提交带图片的问题
    -> Coordinator 调用
    -> Worker 使用 Qwen 原生搜索
    -> Worker 显式读取一个链接
    -> Vision 使用 RapidOCR + Qwen VLM
    -> 返回最终答案
```

验收结果必须能够展示：

- 每个模型 Attempt 的 Token 和 μcredits；
- VLM 的图片 Token 明细；
- 搜索调用次数和搜索 μcredits；
- Web Fetch 的缓存状态、调用次数、字节数和 μcredits；
- OCR 页数和 μcredits；
- Turn 总预占、累计结算和最终释放；
- 用户、工作区、课堂 Bucket 的一致扣减；
- Ledger 中每一笔 reserve、reserve_increment、settle 和 release；
- Provider 账单匹配状态及差额；
- 重放相同事件不会产生第二次扣费。

只有以上数据能够通过同一个 `turn_id`、`reservation_id` 和父子 `operation_id` 关系完整串联，才视为本次开发完成。
