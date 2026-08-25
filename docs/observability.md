# 本地可观测链路

NLP Agent 从 `v0.13.0` 起提供框架无关的本地观测核心。它不启动 HTTP 服务，也不依赖 WebUI、Gateway、Langfuse 或 OpenTelemetry。未来的 Web 层只需要调用 `ObservabilityService`。

## 数据链路

```text
Coordinator / Worker / Model / Tool / Memory / Compression
                         │
                 TelemetryRuntime
            bounded queue + batch writer
                         │
              MySQL telemetry repository
                         │
              ObservabilityService
                  ├── 查询方法
                  └── 实时订阅队列
```

观测记录写入当前部署的 MySQL，由 `NLP_AGENT_DATABASE_URL` 决定目标；测试和生产必须使用不同数据库。Prompt、模型完整输出和工具参数值不会写入观测库；工具仅记录参数键名。

## 关联标识

每个用户 Turn 生成一组不可变的 `TelemetryContext`：

- `request_id`：入口请求标识；
- `trace_id`：完整 Turn 链路；
- `span_id`：当前执行片段；
- `session_id`、`turn_id`：会话和轮次；
- `worker_id`：存在 Worker 时设置。

进程内通过 `contextvars` 传播，Coordinator 到 LangGraph 的边界同时写入 `RunnableConfig.configurable`。所有 Structlog 日志会自动绑定关联标识。

## 已采集 Span

- `coordinator.turn`、`coordinator.worker_resume`；
- `memory.inject`、`memory.curate`；
- `context.prepare`、`worker.context.prepare`，包含压缩前后 Token 和动作；
- `model.request`，包含每次尝试、fallback、思考强度、TTFT、缓存命中/未命中；
- `worker.queue_wait`、`worker.attempt`、`worker.barrier_wait`；
- `tool.<name>`，包含权限、重试、超时、耗时和参数键名。

总响应时间、TTFT、模型/Worker/工具延迟、错误分类和 Token 会汇总到 Trace 或每日指标。

## 查询接口

未来 Gateway 应依赖以下接口，而不是直接读 SQLite：

```python
from core.observability.service import global_observability_service as observability

overview = await observability.overview(days=30)
traces = await observability.traces(limit=100, session_id="...")
detail = await observability.trace("trace-id")
sessions = await observability.sessions(days=30)
usage = await observability.usage(days=30)
errors = await observability.errors(days=30)
events = await observability.events(limit=200, level="error")
health = await observability.health()
```

实时页面可以使用有界订阅队列：

```python
queue = observability.subscribe()
try:
    event = await queue.get()
finally:
    observability.unsubscribe(queue)
```

## 本地调试命令

```powershell
uv run python scripts/observability_cli.py overview --days 30
uv run python scripts/observability_cli.py traces --limit 20
uv run python scripts/observability_cli.py trace <trace-id>
uv run python scripts/observability_cli.py sessions
uv run python scripts/observability_cli.py usage
uv run python scripts/observability_cli.py errors
uv run python scripts/observability_cli.py events --level error
uv run python scripts/observability_cli.py health
```

## Web/Gateway 适配约束

Web 层只负责身份验证、参数校验、序列化和实时推送，不负责产生指标。建议映射为 `/debug/overview`、`/debug/traces`、`/debug/usage`、`/debug/errors`、`/debug/events`，WebSocket 消费 `subscribe()` 返回的队列。

调用方关闭 Agent 运行时时应执行 `await global_telemetry.close()`，确保队列完成刷盘。
