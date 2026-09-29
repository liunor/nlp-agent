# 监控平台后端指标落地说明

本次系统总览装修采用传统后端监控的四类核心信号：请求量（Rate）、错误（Errors）、耗时（Duration）和饱和度（Saturation）。结合项目已有的 Trace / Span / Event / UsageEvent 数据，额外补充用户、工作区、会话、模型、Provider、工具、重试、缓存、计价完整度和遥测管道健康。

## 已落地到系统总览

- 逻辑请求：请求数、成功数、错误/超时数、失败率、P50/P95 响应耗时、P50/P95 首 Token。
- 全用户范围：活跃用户、工作区、Session、按用户的请求/错误率/平均耗时/Token/最后活跃时间。
- 请求标记：Channel、Source、Span kind、结果状态和 Event level。
- 组件与依赖：Model、Worker、Tool 的 Span attempt 调用量、失败率、重试数、平均耗时和 Token；Provider / Model 单独切片。
- 遥测饱和：Telemetry 队列深度/容量、丢弃事件、实时订阅数和状态分布。
- 详细用量：复用 `UsageReadService` 的 canonical input/cache-read/cache-write/output/reasoning/total Token、Credits、计价完整度，以及 User / Workspace / Provider / Model / Purpose 聚合。

逻辑请求和 Span attempt 分开计算：一次用户请求内部的重试不会增加用户请求数或逻辑错误率，但会出现在组件/模型的重试列中。这避免重试放大 SLI，同时保留定位依赖回归所需的信号。

## 后续可接入的采集指标

当前代码已有部分数据源，但没有完整的 Prometheus / OpenTelemetry exporter，因此 CPU、内存、磁盘、数据库连接池、Redis pending age、Worker barrier、Sandbox 心跳和黑盒探针不能在总览中凭空展示。后续可按优先级补充：

1. P0：HTTP 路由请求耗时和 active requests、队列深度/最老任务年龄、模型/工具依赖耗时、retry/fallback/rate-limit、Telemetry 写入失败和 Trace completion ratio。
2. P1：Worker Event Bus、Redis Streams pending/reclaim/DLQ、MySQL 连接池等待、Sandbox capacity deficit / heartbeat / execution outcome。
3. P2：CPU、内存、文件系统、网络、进程重启、指标新鲜度和 cardinality；再结合 SLO 做 error-budget burn rate 告警。

高基数 ID（user/session/turn/trace/span/runtime）和错误消息保留在 Trace/Event 查询中，不作为 Prometheus 标签；指标只使用受控的 operation、status、channel、source、provider、model、tool 和 error type 维度。

## 参考资料

- [Google SRE：Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
- [Google SRE：Service Level Objectives](https://sre.google/sre-book/service-level-objectives/)
- [Google SRE Workbook：Alert on Burn Rate](https://sre.google/workbook/alerting-on-slos/)
- [Prometheus：Instrumentation](https://prometheus.io/docs/practices/instrumentation/)、[Metric types](https://prometheus.io/docs/concepts/metric_types/)、[Alerting](https://prometheus.io/docs/practices/alerting/)
- [OpenTelemetry：HTTP metrics](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)、[Database client metrics](https://opentelemetry.io/docs/specs/semconv/db/database-metrics/)
- [OpenTelemetry：Recording errors](https://opentelemetry.io/docs/specs/semconv/general/recording-errors/)
- [OpenTelemetry：GenAI metrics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)
