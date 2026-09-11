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

若进程在“写入终态”和“追加终态事件”之间退出，重投处理会先通过 `ensure_event` 幂等补齐缺失的 `message.completed`/`turn.completed`、`turn.failed` 或 `turn.cancelled`，确认恢复日志完整后才 ACK；不会为补事件而重新调用模型或工具。

## 状态数据库迁移

`gateway.state.TurnExecutionState` 是 Worker 使用的持久化接口，`gateway.state_factory.build_turn_execution_state` 是组装入口。当前默认实现是 `GatewayRepository`；迁移 MSSQL 时实现该接口，并把 `NLP_AGENT_STATE_FACTORY` 配置为 `package.module:function` 即可替换 Worker 状态适配器，Redis 任务与事件协议不需要改变。

## 扩容与约束

当前受支持的部署保持一个 Worker。Redis Streams 是 **at-least-once** 传输：心跳、终态去重和幂等事件修复覆盖正常长任务及常见崩溃窗口，但 Redis 中断超过租约后仍不能对外部工具副作用提供 exactly-once 保证。迁移共享 MSSQL 后，如需增加 Worker 副本，应先为执行 claim 增加持久 fencing token，并让有副作用的工具按 `turn_id + operation_id` 幂等；在此之前不要通过 `docker compose up --scale nova-worker=N` 扩容执行器。
