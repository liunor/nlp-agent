# 智能体评测系统架构：工具路由 v1

## 1. 目标与边界

第一阶段只回答一个问题：**面对用户问题，智能体是否调用了正确的工具，并且没有调用不相关或禁止的工具。**

判定证据来自现有 Telemetry Trace 中 `kind=tool`、`name=tool.<tool_name>` 的 Span。Gateway 的 `tool.started/tool.completed` 目前只表示 LangGraph tools 节点开始和结束，不包含具体工具名，因此不能作为主判据。

当前工具 Span 可以可靠提供：

- 工具名称、调用顺序和调用次数；
- `ok/error/timeout/denied/cancelled` 状态；
- 重试次数、耗时、来源、风险级别；
- 参数键名。

当前链路有意不保存参数值、完整 Prompt 和模型输出，所以 v1 **不评判参数值是否正确，也不评判最终答案是否正确**。后续通过新的 Evidence Adapter 接入参数脱敏摘要、规则判分、数值判分或模型裁判，不修改测试集运行器的核心接口。

## 2. 设计原则

评测系统作为独立模块放在 `evaluation/`，不把评测规则写进 Agent、Monitor 或工具实现。它只有一个对外 Interface：

```python
report = await evaluator.evaluate(run_spec)
```

调用者只需给出数据集、被测配置和运行参数；会话隔离、等待 Trace 落盘、证据归一化、规则判分、聚合和持久化都隐藏在模块内部。这是一个深 Module：未来增加答案评判、安全评判或性能评判时，调用方仍使用相同 Interface。

## 3. 总体结构

```text
YAML Dataset
    │
    ▼
Dataset Registry ── schema/version validation
    │
    ▼
Evaluation Orchestrator
    ├── Agent Runner Adapter ── 创建隔离 session，提交问题，等待 turn 结束
    ├── Evidence Adapter ────── 根据 turn/trace 读取 Monitor 链路
    ├── Evidence Normalizer ─── Span → ToolCallEvidence
    ├── Judge Registry
    │     ├── ToolRoutingJudge (v1, deterministic)
    │     ├── ArgumentJudge (future)
    │     ├── AnswerJudge (future)
    │     ├── SafetyJudge (future)
    │     └── PerformanceJudge (future)
    └── Result Repository ───── run/case/judge result + artifact refs
              │
              ├── CLI/CI JSON + JUnit
              └── Evaluation Web UI (future)
```

### 3.1 Dataset Registry

职责：加载 YAML/JSONL、校验版本、展开参数化用例、生成不可变的数据集摘要。数据集必须带 `schema_version` 和内容哈希，历史报告永远引用具体版本，不能只引用文件名。

### 3.2 Agent Runner Adapter

每个 case 创建独立 session，写入 `evaluation_run_id`、`case_id`、模型配置、工具目录 revision 和 Prompt revision。默认串行运行以保证可复现；稳定后才开放受控并发。

Runner 必须等待三件事：Turn 到达终态、Trace 完成、Telemetry writer 刷盘。不能用固定 `sleep` 猜测落盘时间。

### 3.3 Evidence Adapter

v1 提供两个 Adapter：

- `LocalTelemetryAdapter`：本地/CI 直接通过 `ObservabilityService` 读取；
- `MonitorHttpAdapter`：独立部署时通过 Monitor 管理员接口读取。

二者输出同一个不可变结构：

```python
class ToolCallEvidence(BaseModel):
    trace_id: str
    turn_id: str
    tool_name: str
    sequence: int
    status: str
    attempts: int
    argument_keys: tuple[str, ...]
    duration_ms: int
```

排序应使用 Span 的 `started_at`，相同时间再以 `span_id` 稳定排序。`running` Span 不能被当作成功调用。

### 3.4 Judge Registry

Judge 是内部 Seam，每个 Judge 接收 case expectation 和归一化 evidence，返回统一的 `JudgeResult`：

```python
class JudgeResult(BaseModel):
    judge: str
    score: float                 # 0..100
    passed: bool
    hard_failures: list[str]
    metrics: dict[str, float]
    evidence_refs: list[str]
    explanation: str
```

Judge 不负责调用 Agent，也不直接查询 SQLite，因而可以用手工 evidence 做纯单元测试。

### 3.5 Result Repository

至少保存：run 配置、dataset hash、case 输入、Trace ID、归一化证据、各 Judge 结果、最终 verdict。原始 Trace 不复制进评测库，只保存引用，避免两份监控事实源漂移。

## 4. 测试集契约

初始数据集位于 `.jbeval/suites/nlp-tool-routing-v1/dataset.yaml`。每个套件独占一个目录，目录名必须等于 YAML 中的 `suite.id`；运行产物位于 `.jbeval/runs/<suite-id>/`，Markdown 报告位于 `evaluation/result/<suite-id>/`。核心字段：

```yaml
schema_version: "1.0"
suite:
  id: nlp-tool-routing-v1
defaults:
  expectation:
    allow_extra_tools: false
cases:
  - id: pr-curve-thresholds
    input: "..."
    tags: [precision-recall, positive, single-tool]
    expectation:
      required_tools: [nlp_precision_recall_curve]
      forbidden_tools: []
      ordered_tools: []
      allowed_tool_statuses: [ok]
```

扩展字段采用“旧运行器拒绝未知的主版本、忽略可选的同主版本字段”策略。未来可以在每个 case 下增加：

- `argument_assertions`：脱敏参数摘要或哈希断言；
- `answer_assertions`：数值、结构、引用和语义评分；
- `safety_assertions`：权限、敏感信息和副作用；
- `performance_budget`：Token、TTFT、总耗时和调用次数；
- `acceptable_routes`：多个等价工具路径。

## 5. 工具路由评判规则

设期望工具集合为 `E`，实际完成或尝试的唯一工具集合为 `A`。重试不会增加集合数量，但会进入效率指标。

### 5.1 硬失败

满足任一条件时 case 直接失败：

1. 缺少 Trace 或 Trace 未完成；
2. 任一 `required_tools` 未调用；
3. 调用了 `forbidden_tools`；
4. `allow_extra_tools=false` 时调用了期望外工具；
5. 工具最终状态不在 `allowed_tool_statuses`；
6. `ordered_tools` 不是实际调用序列的有序子序列；
7. 标记为 `critical` 的 case 出现 timeout、denied 或 error。

对于 `expected_no_tool=true` 的问题，`A` 为空得满分，调用任意工具均失败。这类负样本用于防止“见到 NLP 关键词就乱调工具”。

### 5.2 分数

没有硬失败时，case 分数按以下权重计算：

| 指标 | 权重 | 计算 |
|---|---:|---|
| Required recall | 45 | `|A∩E| / |E|` |
| Tool precision | 25 | `|A∩E| / |A|` |
| 状态正确 | 15 | 期望工具成功结束的比例 |
| 顺序正确 | 10 | 有序约束满足比例；无约束得满分 |
| 调用效率 | 5 | 无重复/额外调用得满分，按冗余次数扣分 |

`E=A=∅` 时 precision 和 recall 均定义为 1。工具错误后成功重试可以通过状态项，但效率项扣分；permission denied 不能算作正确调用。

### 5.3 Case verdict

- `PASS`：无硬失败且得分 ≥ 90；
- `WARN`：无安全/禁止项失败，得分 75–89.99；
- `FAIL`：得分 < 75 或发生普通硬失败；
- `BLOCKED`：评测基础设施失败，例如无 Trace、服务不可用；它不应计成模型能力失败；
- `CRITICAL_FAIL`：critical case 调错工具、调用禁止工具或发生权限/副作用问题。

## 6. Suite 最终判定

报告同时给出分数和门禁，不能只给一个平均分：

- `case_pass_rate`：PASS case 比例；
- `critical_pass_rate`：critical case PASS 比例；
- `macro_tool_f1`：每个 case 的工具 F1 宏平均；
- `required_tool_recall`：所有必须工具的微平均召回率；
- `unexpected_call_rate`：出现额外工具的 case 比例；
- `trace_capture_rate`：成功取得完整 Trace 的比例；
- 按工具、主题、难度、single/multi/no-tool 标签切片的通过率。

建议 CI 门禁：

```text
critical_pass_rate = 100%
case_pass_rate >= 95%
macro_tool_f1 >= 0.90
required_tool_recall >= 0.98
unexpected_call_rate <= 0.02
trace_capture_rate >= 0.99
```

基线比较使用同一 dataset hash、模型配置、Prompt revision 和工具目录 revision。若环境不同，只展示对比，不做回归结论。随机模型每个 case 建议运行 3 次，并同时报告首次通过率与 `pass@3`；CI 快速集可固定温度并运行 1 次。

## 7. 回归测试分层

### 7.1 评测系统自身测试

放入 `tests/test_evaluation_*.py`：

- Dataset schema：必填字段、重复 ID、未知工具、版本兼容；
- Evidence normalizer：Span 排序、重试折叠、状态映射、缺失 Trace；
- ToolRoutingJudge：单工具、多工具、顺序、额外工具、禁止工具、无需工具；
- Aggregator：宏/微指标、critical 门禁、BLOCKED 不污染能力分；
- Result repository：幂等 run、dataset hash、Trace 引用。

这些测试全部使用内存 evidence，不启动模型，必须快速且确定。

### 7.2 集成测试

使用 Fake Agent Runner 产生受控 Trace，验证：

```text
case → session/turn → telemetry span → normalizer → judge → report
```

至少覆盖正常完成、工具 error、timeout、denied、Telemetry 延迟落盘和 Trace 缺失。

### 7.3 Agent 回归

- PR 快速集：每个工具 2 个正样本、2 个负样本、核心多工具样本，固定模型配置；
- 主分支完整集：运行当前 YAML 的所有 case；
- 夜间稳定性集：每个 case 3 次，统计方差、pass@3、Token 与延迟；
- 新工具接入门禁：新增工具必须同时添加正样本、相邻工具辨析样本、无需调用负样本。

统一命令：

```powershell
uv run python -m evaluation validate .jbeval/suites/nlp-tool-routing-v1/dataset.yaml
uv run python -m evaluation run nlp-tool-routing-v1 --live --case bleu-course-example
uv run python -m evaluation run nlp-tool-routing-v1 --live
uv run pytest tests/test_evaluation_*.py
uv run pytest
```

`run` 一定要求 `--live`。先启动 Web 服务和 Monitor 服务；运行器通过 `--web-url` 创建真实 session、提交真实问题，并通过 `--monitor-url` 读取同一 Turn 的真实 Telemetry Trace。它不会启动第二个 Gateway，因此保持单进程 Gateway 所有权；这会产生模型 API 费用。建议先用单个 `--case` 冒烟验证，再运行完整套件。运行结果默认保存到 `.jbeval/runs/<suite-id>/`；`--output` 可覆盖该位置；运行产物不纳入版本管理。

生产认证模式下，在当前 PowerShell 或项目根目录的 gitignored `.env` 中设置专用
评测账号。密码只通过环境变量配置，不得写入命令行、数据集或报告：

```powershell
$env:NLP_AGENT_EVALUATION_USERNAME='evaluation-user'
$env:NLP_AGENT_EVALUATION_PASSWORD='<password>'
```

该账号必须能在目标评测工作区创建会话；读取 Monitor 证据还需要
`system:runtime:monitor` 权限。评测器登录 Web 后会把同一个数据库会话 Cookie
复制到 Monitor 客户端，不会创建第二套认证会话。

前端若增加评测报告页面，再执行：

```powershell
cd webui
npm run test -- --run
npm run lint
npm run build
```

## 8. 初始实施顺序

1. 实现数据集 Pydantic schema 与 `validate` 命令；
2. 实现 `ToolCallEvidence` 和两个 Evidence Adapter；
3. 实现纯规则 `ToolRoutingJudge` 及聚合器；
4. 实现隔离 Runner、Telemetry flush/关联机制；
5. 输出 JSON、Markdown、JUnit 报告并接入 CI；
6. 再增加参数、答案、安全和性能 Judge。

首版不要引入 LLM-as-a-Judge。工具路由是结构化事实，规则判定更便宜、稳定、可解释；等评测目标扩展到开放答案质量时，再把模型裁判作为一个新 Adapter 接入。
