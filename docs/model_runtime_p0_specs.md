# Model Runtime P0 规范文档：Kimi 与 GLM 模型接入参数、计费与错误码表

本文档作为《扩展模型.md》中 P0 准备阶段的固化技术规范，明确记录 Moonshot (Kimi) 与 Zhipu BigModel (GLM) 两大国内主流大模型平台的模型规格、计费标准、思考推理控制规则、流式机制及错误码映射规范。

## 0. 证据与核验记录

- **核验日期**：2026-09-05（Asia/Shanghai）。
- **资料来源**：以下链接均为厂商官方文档；模型规格、请求参数、用量字段与错误码以链接页面为准。
  - [Kimi K2.6 模型与快速开始](https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart)：256K 上下文、Kimi API 端点、`thinking` 参数、最大 `max_tokens`。
  - [Kimi Chat Completions API](https://platform.kimi.com/docs/api/chat)：`usage.cached_tokens`、`reasoning_content` 与工具调用约束。
  - [Kimi 模型列表](https://platform.kimi.com/docs/models)：当前可用模型与下线/弃用说明，避免把不同系列的模型混写为已全面下线。
  - [Kimi K2.6 定价](https://platform.kimi.com/docs/pricing/chat-k26)：K2.6 的输入、输出和缓存价格入口。
  - [智谱 GLM-5.3 模型文档](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)：1M 上下文、128K 输出、强制思考、`reasoning_effort` 与 API 端点。
  - [智谱 GLM-5.2 模型文档](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)：1M 上下文、128K 输出及 GLM-5.2 能力说明。
  - [智谱 GLM-5.3 迁移指南](https://docs.bigmodel.cn/cn/guide/start/migrate-to-glm-new)：`tool_stream=true`、流式工具调用和参数迁移规则。
  - [智谱 BigModel 价格页](https://bigmodel.cn/pricing)：GLM-5.3 与 GLM-5.2 当前公开的输入、输出与缓存命中价格。
  - [智谱 API 错误码](https://docs.bigmodel.cn/cn/api/api-code)：HTTP 状态码与业务错误码的当前官方映射。
  - [阿里云 qwen3-vl-plus 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-vl-plus)：视觉依赖模型的上下文、分档 Token 价格和缓存价格。
  - [阿里云百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)：Qwen 3.8 Max 与 Qwen 3.7 Plus 的分模式标准价。
  - [阿里云百炼联网搜索计费](https://help.aliyun.com/zh/model-studio/web-search)：华北 2 地域 `turbo` 策略的搜索调用价格。
  - [DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：V4 Flash/Pro 的高峰、空闲输入输出与缓存命中价格。
- **账号级验证**：2026-09-05 的安全检查仅记录是否配置，不输出密钥值。`GLM_API_KEY` 已在本地 `.env` 配置并通过账号验证：List Models 返回 HTTP 200，账号可见 `glm-5.3` 与 `glm-5.2`；两模型的真实 Chat Completions 冒烟均成功。`KIMI_API_KEY` 在 `.env`、当前进程、用户级和机器级环境中均未配置，Kimi 真实 API 冒烟仍被阻塞。

---

## 1. 基础连接与平台配置规范

| 平台 Provider | API 环境变量名 | API 端点 Base URL | 适配器 Adapter | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| **Kimi (Moonshot)** | `KIMI_API_KEY` | `https://api.moonshot.cn/v1` | `kimi` (`KimiAdapter`) | 采用 OpenAI 兼容协议拓展，国内平台端点 |
| **GLM (智谱清言)** | `GLM_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | `glm` (`GLMAdapter`) | 采用 PAAS v4 兼容协议拓展，国内平台端点 |

### 环境变量与配置绑定
- 在 `configs/settings.py` 的 `Settings` 类中绑定 `KIMI_API_KEY: str = ""` 与 `GLM_API_KEY: str = ""`。
- 在 `.env-example` 与本地 `.env` 文件中声明对应变量占位与说明。
- 平台密钥状态识别：`Settings._get_llm_config()` 通过 `api_key_configured = bool(getattr(self, env_name, ""))` 自动识别两家厂商的密钥配置就绪状态。

---

## 2. 模型规格与能力参数

| 厂商 | 模型 ID (Model ID) | 上下文窗口 (Context Window) | 最大输出限制 (Max Output) | 支持角色 / Preset |
| :--- | :--- | :--- | :--- | :--- |
| **Kimi** | `kimi-k2.6` | 256k (262,144 tokens) | 32k (32,768 tokens) | coordinator, worker, utility |
| **GLM** | `glm-5.3` | 1M (1,048,576 tokens) | 128k (131,072 tokens) | coordinator, worker |
| **GLM** | `glm-5.2` | 1M (1,048,576 tokens) | 128k (131,072 tokens) | utility (非思考模式轻量任务) |

> **注意 (模型 ID 约束)**：
> - 本项目的 P0 角色映射统一选用 `kimi-k2.6`，但不能据此推断 Kimi 其他系列全部下线；当前官方模型列表仍列出 `kimi-k2.5` 与 `moonshot-v1`，而部分 `kimi-k2` preview 型号另有下线说明。
> - 配置中禁止使用已在官方模型列表标为下线或弃用的标识；最终可用性应以 List Models API 和账号权限为准。
> - GLM Coordinator 与 Worker 角色统一使用具备强推理能力的 `glm-5.3`；Utility 角色（如记忆压缩、摘要提取）使用具备高确定性与低延迟的 `glm-5.2`。

---

## 3. 定价标准与计费归一化

单价均以平台官方标准（人民币 元/MTok）为准，写入系统 `nlp_pricing_rules` 规则表。当前额度换算政策明确为 **1 credit = ¥1，1 credit = 1,000,000 μcredits**。因此 ¥6.50/MTok 写为 6,500,000 μcredits/MTok。价格是动态运营数据，落库前必须记录当日价格页核验时间。

截至 2026-09-05，Kimi K2.6 的官方价格入口支持核对表中数值；智谱官方价格页的当前前端数据明确列出 GLM-5.3 与 GLM-5.2 的输入 ¥8、输出 ¥28、缓存命中 ¥2。GLM-5.3 模型文档同时确认其 1M 上下文、128K 最大输出与强制思考规则。

| 厂商 | 模型 ID | 定价键 (Pricing Key) | 输入单价 (¥/MTok) | 输出单价 (¥/MTok) | 缓存命中单价 (¥/MTok) | 计费减法分区与说明 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Kimi** | `kimi-k2.6` | `kimi/kimi-k2.6` | ¥6.50 | ¥27.00 | ¥1.10 | Cache-miss 自动落入 ordinary input 计价；Thinking tokens 计入输出；顶层 `cached_tokens` 归一化 |
| **GLM** | `glm-5.3` | `glm/glm-5.3` | ¥8.00 | ¥28.00 | ¥2.00 | 以官方价格页当前公开值为准；Cache-miss 自动落入 ordinary input 计价；嵌套 `prompt_tokens_details.cached_tokens` 归一化 |
| **GLM** | `glm-5.2` | `glm/glm-5.2` | ¥8.00 | ¥28.00 | ¥2.00 | 以官方价格页当前公开值为准；Cache-miss 自动落入 ordinary input 计价；嵌套 `prompt_tokens_details.cached_tokens` 归一化 |

内置目录为 `server/quota/builtin_pricing.py`，版本号为 `official-cny-2026-09-05`，基准生效时间为 2026-09-05 00:00:00（Asia/Shanghai）。`python main.py bootstrap-db` 在迁移后自动安装目录；`scripts/seed_extended_model_pricing.py` 复用同一目录，只保留预览和人工执行能力。目录覆盖当前生产可达模型、Qwen-VL 视觉字段、Qwen 原生搜索以及本地 OCR/链接读取的显式产品政策。重复执行会精确校验同版本费率；系统管理的开放旧版本会在同一事务中闭合，人工规则发生重叠则阻止部署。

Qwen-VL 规则按本项目图片请求适用的首档（输入不超过 32K）记录输入 ¥1、输出 ¥10、缓存命中 ¥0.2/MTok；视觉 Token 与普通输入同价，`image_units` 价格只用于请求前的保守额度预留，响应后由实际视觉 Token 替代。DeepSeek 静态目录采用官方高峰价格；Qwen 3.7 Plus 因思考和非思考 preset 共用一个 `pricing_key`，采用较高的思考模式输入价进行保守准入。临时优惠不进入内置目录。

### 用量字段归一化 (`core/model_runtime/normalization.py`)
1. **Kimi**：Moonshot 在响应的 `usage` 对象中返回 `cached_tokens`；在本项目归一化函数接收的 usage 映射中，该字段表现为顶层 `cached_tokens`。现有归一化逻辑（`normalization.py:62-73`）已自动兼容，提取为 `cached_input_tokens`。
2. **GLM**：智谱在 `usage.prompt_tokens_details.cached_tokens` 中返回缓存命中量，归一化逻辑同样已完全覆盖。
3. **计费不变量**：
   - $\text{input\_tokens} = \text{ordinary\_input\_tokens} + \text{cached\_input\_tokens}$
   - $\text{cache\_miss\_tokens} = \text{input\_tokens} - \text{cached\_input\_tokens}$，直接按输入单价计费，无需单独的 cache-miss 价格字段。

---

## 4. 思考推理配置与协议约束

### 4.1 Kimi (kimi-k2.6)
1. **Thinking 结构**：
   通过请求体 `extra_body` 控制：
   ```json
   {
     "thinking": {
       "type": "enabled" | "disabled",
       "keep": null
     }
   }
   ```
   思考上下文跨轮不保留（`keep: null`，与 DeepSeek 策略一致）。
2. **禁止参数**：
   - `kimi-k2.6` 思考模式下**严格禁止**传递 `temperature`、`top_p`、`n` 参数。
   - `kimi-k2.6` 不支持 `effort` 参数，构造请求时**严禁发送** `effort` 字段。
   - Adapter 构建防御：`KimiAdapter.build()` 必须执行 Fail-Fast 检查，若 Preset 中显式配置了 `temperature` 或 `top_p`，必须直接抛出异常，杜绝静默忽略或产生 400 请求报错。
3. **Token 预算与 Preset**：
   - coordinator / worker preset 的 `max_output_tokens` 必须 $\ge 16000$（Kimi 将 reasoning tokens 计入 max_tokens 预算）。
   - utility preset 不得沿用既有的 `temperature: 0.1` 模板。
4. **Tool Loop 历史回传 (Reasoning Pass-Back)**：
   - 遵循 DeepSeek 模式：`KimiChatModel._get_request_payload` 仅对包含 `tool_calls` 的历史 Assistant 消息回传 `reasoning_content`（保证工具调用循环不被服务端截断）。
   - 普通轮次（不包含工具调用的助手消息）不注入 `reasoning_content`，以保证请求前缀一致性并最大化 Prompt Cache 命中率。

### 4.2 GLM (glm-5.3 & glm-5.2)
1. **思考强制规则 (glm-5.3 Thinking Enforcement)**：
   - `glm-5.3` 服务端强制要求启用思考模式。Coordinator 和 Worker 角色的**内部 Preset**必须设置 `thinking.enabled: true`，并使用 `thinking.effort: "max" | "high"`；Adapter 发给厂商的请求应映射为 `thinking: { "type": "enabled" }` 与独立的 `reasoning_effort` 字段，不应把内部字段原样发送。
   - 禁止创建任何 `glm-5.3 + disabled` 的 Preset。
   - Adapter 构建防御：`GLMAdapter.build()` 针对 `glm-5.3*` 检查 `thinking.enabled`，若为 `False` 直接抛出构建异常（Fail-Fast），阻止非法配置加载。
   - Utility 角色使用 `glm-5.2`，内部 Preset 设置 `thinking.enabled: false`，由 Adapter 映射为厂商请求的 `thinking: { "type": "disabled" }`，允许配置 `temperature: 0.1`。
2. **Effort 参数映射**：
   - `glm-5.3` 仅接受 `low` / `high` / `max`：
     - 系统内部 `low` $\to$ `low`
     - 系统内部 `medium` $\to$ `high`
     - 系统内部 `high` $\to$ `high`
     - 系统内部 `max` $\to$ `max`
   - `glm-5.2`：可直接透传 `none` / `low` / `medium` / `high` / `max`（服务端自动处理映射）。
3. **工具流生成机制 (`tool_stream: true`)**：
   - `GLMAdapter` 在向 `glm-5.3` 与 `glm-5.2` 发起包含工具调用的请求时，在 `extra_body` 中设置 `tool_stream: true`。
   - **目的**：智谱在模型生成大段复杂工具参数时，默认可能产生较长无 Chunk 静默期。开启 `tool_stream: true` 使模型增量吐出工具参数 Chunk，防止客户端触发 `stream_idle_s` 流空闲超时。
   - **回退兜底**：若冒烟测试中 LangChain `ChatOpenAI` 无法正确合并增量 `tool_call_chunks`，则退回 `tool_stream: false` 并调大 Worker Preset 的 `stream_idle_s`。
4. **Tool Loop 历史回传**：
   - 与 Kimi/DeepSeek 相同，`GLMChatModel._get_request_payload` 仅在 Assistant 消息包含 `tool_calls` 时回传 `reasoning_content`（满足 GLM Interleaved Thinking 要求），普通轮次不回传。

---

## 5. GLM 错误码与运行时错误分类映射表

智谱 BigModel 平台在请求异常时，除返回 HTTP 状态码外，通常在响应体 `error.code` 中返回特定整数错误码。根据 `core/model_runtime/runtime.py:classify_model_error()` 的判定体系，错误码与处理策略必须遵循以下映射：

### 5.1 核心重点错误码（必须在前置逻辑中精准锁定）

| 平台错误码 (Code) | HTTP 状态码 | 含义描述 | 映射标准化分类 (`error_kind`) | 是否可重试 (`retryable`) | 处理与判定要求 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1113** | **429** | **账户欠费** (Account overdue) | `upstream_provider_quota_exhausted` | **False** (不可重试) | **【极高优先级前置拦截】**<br>智谱余额不足时返回 HTTP 429。必须在通用 429 判断之前检查业务码，避免无效重试与回退。 |
| **1261** | **400** | **Prompt 超长** (Prompt too long) | `upstream_context_length_exceeded` | **False** (不可重试) | 必须区别于通用 `upstream_invalid_request`，确保调用方进入截断/压缩链路。 |
| **1301** | **400** | **输入或生成内容触发安全策略** (Sensitive content) | `upstream_unknown`（建议后续新增 `upstream_content_filtered`） | **False** (不可重试) | 不得按系统过载处理，也不得重试同一请求。当前运行时没有专用内容拦截分类，先使用不可重试的 `upstream_unknown`。 |
| **1302** | **429** | **账户达到速率限制** (Rate limit) | `upstream_rate_limited` | **True** (可重试) | 可读取响应头 `Retry-After` 进行退避；仍需遵守本地最大重试次数。 |
| **1305** | **429** | **模型当前访问量过大** (Model traffic too high) | `upstream_overloaded` | **True** (可重试) | 属于厂商临时拥堵，可重试；不应描述为账户永久配额耗尽。 |
| **1308** | **429** | **达到使用上限，等待 `next_flush_time` 重置** (Usage limit reached) | `upstream_provider_quota_exhausted` | **False** (不可立即重试) | 这不是通用服务过载。必须停止当前重试链，并尽可能保留响应中的重置时间；若未来新增 `upstream_provider_usage_limit`，应迁移到该专用分类。 |
| **1309** | **429** | **GLM Coding Plan 已到期** | `upstream_provider_quota_exhausted` | **False** (不可重试) | 属于订阅权益问题，不应触发模型重试。 |
| **1310** | **429** | **达到每周/每月使用上限** | `upstream_provider_quota_exhausted` | **False** (不可立即重试) | 记录重置时间；不得按 `upstream_rate_limited` 进行短退避重试。 |
| **1311** | **429** | **当前订阅未开放该模型权限** | `upstream_provider_quota_exhausted` | **False** (不可重试) | 应提示更换有权限的模型或订阅，不应回退到同一权益范围内的候选。 |

### 5.2 完整 BigModel 错误码参考与映射规范

| 错误码 (Code) | 常见 HTTP 状态 | 官方描述 | 映射分类 (`error_kind`) | 重试决策 |
| :--- | :--- | :--- | :--- | :--- |
| `1000` / `1001` / `1003` / `1005` | 401 | 身份验证失败、缺失、过期或需要二次认证 | `upstream_auth_failed` | 不可重试 |
| `1113` | 429 | 账户欠费 | `upstream_provider_quota_exhausted` | 不可重试 |
| `1200` | 500 | API 调用失败 | `upstream_overloaded` | 可重试 |
| `1210` / `1212` / `1213` / `1214` / `1215` | 400 | API 调用参数、方法、必填字段或字段值错误 | `upstream_invalid_request` | 不可重试 |
| `1211` | 400 | 模型不存在 | `upstream_model_unavailable` | 不可重试 |
| `1220` | 403 | 无权访问 API | `upstream_auth_failed` | 不可重试 |
| `1221` / `1222` | 400 | API 已下线或不存在 | `upstream_model_unavailable` | 不可重试 |
| `1230` / `1234` | 500 | API 流程或网络错误 | `upstream_overloaded` | 可重试（以原始响应为准） |
| `1261` | 400 | Prompt 超长 | `upstream_context_length_exceeded` | 不可重试 |
| `1301` | 400 | 输入或生成内容触发安全策略 | `upstream_unknown`（建议后续新增 `upstream_content_filtered`） | 不可重试 |
| `1302` | 429 | 账户达到速率限制 | `upstream_rate_limited` | 可重试 |
| `1305` | 429 | 模型当前访问量过大 | `upstream_overloaded` | 可重试 |
| `1308` | 429 | 达到使用上限，等待重置 | `upstream_provider_quota_exhausted` | 不可立即重试 |
| `1309` | 429 | GLM Coding Plan 已到期 | `upstream_provider_quota_exhausted` | 不可重试 |
| `1310` | 429 | 达到每周/每月使用上限 | `upstream_provider_quota_exhausted` | 不可立即重试 |
| `1311` | 429 | 订阅暂未开放指定模型权限 | `upstream_provider_quota_exhausted` | 不可重试 |
| `1313` | 429 | 公平使用策略限制请求频率 | `upstream_provider_quota_exhausted` | 不可立即重试 |
| `1314` / `1315` | 429 | 企业套餐失效或 API Key 产品类型不匹配 | `upstream_provider_quota_exhausted` | 不可重试 |
| `1316` / `1317` / `1318` / `1319` / `1320` / `1321` | 429 | 5 小时、7 天或企业/子账号消费上限 | `upstream_provider_quota_exhausted` | 不可立即重试 |

> `1002`、`1303`、`1304`、`1306`、`1114`、`1115` 未出现在当前官方错误码页的列表中。本项目不得在没有真实响应证据的情况下把它们写入确定性映射；如 P4 实测出现，应保存脱敏后的原始 HTTP 状态、业务码、消息和响应 ID 后再补表。

### 5.3 `classify_model_error` 实现逻辑规范 (P2 实现基准)

在 P2 阶段更新 `core/model_runtime/runtime.py` 的 `classify_model_error()` 时，必须将 GLM 专用数字错误码判定前置于通用 HTTP 状态码拦截，执行顺序如下：

```python
# 1. 优先提取数字或字符串形式的平台错误码
code_str = str(details.get("code", code) or code).strip()

# 2. GLM 专有数字错误码前置拦截（必须在 status == 429 与 status == 400 之前）
if code_str == "1113":
    # 智谱欠费返回 HTTP 429，前置拦截防止被误判为 upstream_rate_limited
    return ErrorDecision(False, "upstream_provider_quota_exhausted")
if code_str == "1261":
    # Prompt 超长返回 HTTP 400，前置拦截防止被归为通用 upstream_invalid_request
    return ErrorDecision(False, "upstream_context_length_exceeded")
if code_str == "1301":
    # 敏感内容不是系统过载；当前没有专用 content_filtered 分类
    return ErrorDecision(False, "upstream_unknown")
if code_str in {"1210", "1212", "1213", "1214", "1215"}:
    return ErrorDecision(False, "upstream_invalid_request")
if code_str in {"1211", "1221", "1222"}:
    return ErrorDecision(False, "upstream_model_unavailable")
if code_str == "1302":
    # 账户速率限制，可按 Retry-After 退避
    return ErrorDecision(True, "upstream_rate_limited", retry_after)
if code_str == "1305":
    # 模型访问量过大，属于可重试的临时过载
    return ErrorDecision(True, "upstream_overloaded", retry_after)
if code_str in {
    "1313", "1314", "1315", "1316", "1317", "1318", "1319", "1320", "1321",
    "1308", "1309", "1310", "1311",
}:
    # 账户/订阅使用上限或权益限制；禁止短退避重试
    return ErrorDecision(False, "upstream_provider_quota_exhausted")
```

`1308`、`1310` 等响应可能包含下一次重置时间。P2 应将其纳入结构化诊断或错误元数据，但不能把等待时间伪装成普通 `Retry-After` 并在当前调用链内重复尝试。

---

## 6. 流式终态异常判定规范 (`ERROR_FINISH_REASONS`)

在流式响应正常结束（流连接未断开但模型提前终止生成）时，`ResilientChatModel` 通过检测响应中的 `finish_reason`，判定是否需要转为错误抛出：

```python
GLMChatModel.ERROR_FINISH_REASONS = {
    "model_context_window_exceeded": "upstream_context_length_exceeded",  # 不可重试
    "network_error": "upstream_overloaded",                                # 可重试
    "sensitive": "upstream_unknown",                                      # 不可重试
}
```

- **执行时机**：在 `astream` 与 `ainvoke` 内部流式读取循环结束后（`received=True` 且成功 `return` 之前）。
- **生命周期语义**：
  - 若已输出正文或工具调用，抛出 `StreamInterruptedError`（不进行重试/回退，不向前端拼接新模型生成）；
  - 若仅输出 reasoning 且尚未输出正文或工具调用，遇到可重试错误时允许透明重试或 Fallback；
  - 若未输出任何可见内容，命中重试异常则触发透明重试或 Fallback；
  - `finish_reason` 正常进入该 Attempt 的 `InvocationOutcome` 记录。

---

## 7. P0 验收与验证基线

1. **配置完备性**：
   - `configs/settings.py` 成功声明 `KIMI_API_KEY` 与 `GLM_API_KEY`；
   - `.env-example` 与 `.env` 包含两个密钥的注释与占位。
2. **环境可用性**：
   - Windows PowerShell 7 环境下执行 `uv run pytest tests/test_model_runtime.py` 返回 Exit Code 0 且全部通过。
3. **证据与账号验证**：
   - 文档中的模型、参数、用量和错误码均有官方来源链接；价格应在实际落库前再次记录官方价格页的核验时间。
   - Kimi 与 GLM 各执行一次最小成本真实 API 请求，记录 HTTP 状态、业务错误码、模型、响应 ID 和用量字段；不得记录 API Key。
   - 当前 GLM 账号验证已完成；Kimi 因未配置密钥仍未完成，不能把两家账号验证整体标记为完全通过。

## 8. P4 真实冒烟与回归记录

### 8.1 GLM 账号与真实请求

- **模型列表**：2026-09-05 调用国内端点 `GET /api/paas/v4/models` 返回 HTTP 200，共返回 10 个模型；账号可见 `glm-5.3` 与 `glm-5.2`。
- **GLM-5.3 工具流**：请求体已验证 `thinking.type=enabled`、`reasoning_effort=high`、`tool_stream=true`。真实流返回 10 个 `tool_call_chunks`，LangChain `ChatOpenAI` 成功合并为一个参数为 `a=2, b=3` 的 `add_numbers` 调用，终态 `finish_reason=tool_calls`。
- **GLM-5.3 用量**：真实 Runtime 事件为 input 192、cached input 128、output 18、total 210；`source=provider`、`semantics=final`，带脱敏验证后的 Provider response ID。该短工具任务没有返回可见 `reasoning_content`，但请求侧强制思考参数已生效；不得把“未展示推理文本”误写成“未启用思考”。
- **GLM-5.2 Utility**：请求体已验证 `thinking.type=disabled`、`temperature=0.1`。真实调用终态 `finish_reason=stop`，Runtime 事件为 input 11、cached input 0、output 1、total 12；`source=provider`、`semantics=final`，带脱敏验证后的 Provider response ID。
- **异常场景边界**：不得为了制造 `1113` 欠费而耗尽或破坏有效账号，也不得为了制造 `1261` 向厂商发送接近 1M token 的高成本请求。两者的数字码优先分类、不可重试语义与异常 finish reason 已由 `tests/test_glm_adapter.py` 的确定性单元测试覆盖；只有取得厂商脱敏错误响应样本后，才能补充真实 HTTP 证据。

### 8.2 Kimi 阻塞项

`scripts/smoke_extended_models.py` 已实现 Kimi 思考开/关、流式 reasoning 先于 content、工具循环 reasoning 回传及原始 `usage.cached_tokens` 字段检查；当前因 `KIMI_API_KEY` 未配置，在发起网络请求前 fail-fast。补齐密钥后运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_extended_models --provider kimi
```

### 8.3 回归结果

- GLM 真实冒烟通过，两个用量事件均满足 exact（Provider 原始用量、final 语义、总量守恒、响应 ID 存在）。
- 定价初始化的版本化、三组精确费率与幂等测试通过；GLM-5.3 定价已按官方价格页当前前端数据补齐。
- 后端全量回归在迁移到 head 且写入已核实定价规则的随机隔离 MySQL 临时库中执行：**1152 项通过、14 项按环境跳过**。为消除 Windows 并发测试中可复现的覆盖文件初始化竞态，`core/runtime_config.py` 增加了进程内锁，并完成 30 轮定向压力复测。
- 前端全量 Vitest（含真实 FastAPI HTTP/WebSocket 集成）为 **53 个测试文件、359 项测试全部通过**；`npm run lint`、`npm run typecheck`、`npm run build` 均通过。
- 全仓 Ruff 检查通过。隔离数据库与 pytest 临时目录均已删除。现有本地 `nlp_agent` 开发库已在备份后从 Alembic `20260820_24` 升级至 `20260904_49_billable_features`（head），并成功写入 `official-cny-2026-09-05` 版本的 Kimi K2.6、GLM-5.3、GLM-5.2 三条定价规则；二次执行全部返回 `already_present`，幂等验证通过。升级前备份保存在 `.data/backups/nlp_agent_before_20260905_111701.sql`。
