# Redis 与独立 Worker 部署

生产拓扑由 `nova-web`、`nova-worker` 和 Redis 三个运行单元组成。Web 负责鉴权、Turn 创建、HTTP/WebSocket 与事件恢复；Worker 负责 LangGraph、模型和工具执行。

## 启动

```powershell
docker compose up -d redis nova-web nova-worker
docker compose ps
```

关键环境变量：

- `NLP_AGENT_GATEWAY_TRANSPORT=redis`
- `NLP_AGENT_REDIS_URL=redis://redis:6379/0`

Redis Streams 使用 consumer group 投递 Turn。成功执行后 Worker 才 ACK；执行中的 Worker 会定期续租 pending 消息，进程失联且超过 `redis_reclaim_idle_ms` 后由新的 Worker 通过 `XAUTOCLAIM` 重新认领。MySQL claim generation 决定执行所有权，因此旧投递会在终态补齐后 ACK，不会因容器重建和 consumer 名变化永久堆积。取消同时写入带 TTL 的 Redis 标记并广播控制消息：排队中的任务不会漏掉取消，运行中的任务可以立即终止，取消落库后消息仍会 ACK。

Worker 会持续重试短暂失败的租约续期和消费迭代。无法解码或协议版本不受支持的任务会写入 `redis_dead_letter_stream` 后 ACK，避免 poison message 反复终止消费者。Web 事件订阅和 Worker 控制订阅断线后会自动重连；事件订阅断线时及重新订阅成功后都会中断该窗口内的 WebSocket，强制客户端从持久序列恢复。单条坏 Pub/Sub 消息只会被隔离。

Worker 产生的事件先写入状态端口，再尽力发送到 Redis Pub/Sub。Pub/Sub 失败不会回滚已完成 Turn 或阻止 ACK；浏览器断线、Redis 短暂不可用或消息丢失后仍通过持久事件日志和 `stream.resume` 恢复。

MySQL 终态与必要终态事件在同一事务提交。对于历史遗留的缺事件数据，重投处理仍通过 `ensure_event` 幂等补齐 `message.completed`/`turn.completed`、`turn.failed` 或 `turn.cancelled`，确认恢复日志完整后才 ACK；不会为补事件而重新调用模型或工具。

## 状态数据库迁移

`gateway.state.TurnExecutionState` 是 Worker 使用的持久化接口，`gateway.state_factory.build_turn_execution_state` 是组装入口。生产使用 MySQL 与 Alembic；不要通过更换状态工厂绕过 claim、事务终态或事件去重。

## 扩容与约束

生产仍保持一个 Worker、一个执行槽，Compose 默认 concurrency 为 1。只有隔离 test/staging 才逐步设置 `NLP_AGENT_WORKER_CONCURRENCY=4` 并验证 1×4、2×4；当前补丁不授权直接扩容生产。

Redis Streams 是 **at-least-once** 传输。当前 SQL 终态、事件与学习状态使用 generation/owner/lease fencing；生产允许 pending reclaim，任一执行槽释放后可继续拉取。它们不等于外部模型和工具副作用 exactly-once：工具幂等、checkpoint/transcript 审计、detached background resume 权威生命周期与模型额度账本仍需补齐。

Web、两个 Worker、Monitor、Sandbox manager 的同步及异步池合计上限为 59，含 10 条运维预留为 69，当前 MySQL `max_connections=100`。同步辅助存储按进程共享 engine；pool timeout 为 5 秒。修改副本数、进程数或池配置后必须重新核算。

本轮实现、测试结果和未完成 gate 见 [T07 复审验证记录](specs/t07-review-fix-verification.md)。未通过完整 50/30 负载、故障注入和额度一致性验收前，不执行 `docker compose up --scale nova-worker=2`。
