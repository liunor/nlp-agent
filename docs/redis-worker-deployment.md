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

通用 Compose 和生产示例的默认值不代表 TEST 的实际发布档位。发布与扩容以生效配置、实际容器和 TTL 心跳为准，不能从旧实施计划中的 1×1 或 2×4 推断当前状态。

Redis Streams 是 at-least-once 传输；MySQL generation/owner/lease fencing 保护终态和学习状态，但不等于外部模型、工具副作用 exactly-once。detached background resume 的独立持久生命周期、工具副作用幂等及 checkpoint/transcript 审计仍需独立验证，不能因旧规格删除就视为完成。

修改 Worker 数量、并发或进程数时，合计 Web、Worker、Monitor、Manager 的全部同步/异步连接池和 overflow，再加入运维预留，与真实 MySQL max_connections 对照。记录 acquire 等待、锁等待、pool timeout、队列深度/年龄及现有会话可用性。

## Outbox 消息路由

| topic | 目的地 |
| --- | --- |
| turn.dispatch | 编码后的对话任务流 |
| authorization.changed | 权限变更 Pub/Sub |
| sandbox.lease.released / sandbox.lease.revoked | `<任务流名称>:sandbox-events` |
| 未知 topic 或缺少编码任务 | 保留 MySQL outbox 行并重试，不投入任务流 |

沙箱事件流保留 topic、payload、outbox ID，近似 MAXLEN 10000，不能替代永久审计。发布与确认之间退出仍可造成重复通知。执行许可仍按数据库会话、授权与租约核对，Manager reconcile 回收失效 runtime；单会话退出不等于释放该用户所有会话。

## 容量目标与未完成验收

对话目标为 41 人同时发消息时正文首段 P95 不超过 20 秒；HTTP 202、排队提示、思考或任务开始事件均不算正文首段。另报完整回答耗时、完成率、最慢值和普通会话可用性。短问题结果不能推广到工具、长上下文或代码负载。

2026-10 的历史记录包含失败与部分通过，以下只保留防止误操作所需的结论，完整现场日志和中间实验可查 Git 历史：

| 场景 | 历史结论与边界 |
| --- | --- |
| TEST 2×8 Worker 试验 | 41 用户阶段出现终态及 WebSocket 失败，该配置被拒绝并恢复 |
| PR #268 选择的 TEST 2×16 | 41/41 完成，25/41 在 20 秒内看到正文，首段 P95 25.016 秒；未达到严格 20 秒目标 |
| 首次 2×24 尝试 | 应用配置校验阻止启动，已恢复 2×16；没有吞吐验收结果 |
| 后续并发校验上限提高到 32 | 仅允许有界比较，不代表部署选择或主机容量承诺 |

选定 2×16 的 TEST 发布使用每 Worker 异步 pool/overflow 8/2、共享同步 pool/overflow 16/2、pool timeout 5 秒。这是历史选定方案，后续部署必须从现行发布脚本和现场配置重新确认，不能据此直接修改正式站。

后续验收需覆盖真实 MySQL 迁移/并发、Redis 中断、Worker 强杀/接管、排队和运行取消、迟到用量结算、无重复终态和额度泄漏，以及完整课堂时段。保留 tests/load 的基线/修复后对比能力；模拟测试和心跳只能分别证明其覆盖范围。

## 沙箱与对话资源分别验收

沙箱内核保留数量、代码执行并发和聊天 Worker 槽是三个限制。一次代码执行结束不等于内核租约释放，少量沙箱通过不能推断全班独立交互式环境容量。

2026-10-25 的有限现场对照采用 512 MiB / 1 CPU、TEST 最多 4 个内核、整机受管上限 6、代码并发 2、动态预热目标 1（最少 1、最多 2）、内存预留 2560 MiB、磁盘预留 15 GiB；4 用户完成 8 次小型混合执行。此前 3072 MiB 预留曾在第 3 人领取时触发内存保护。该记录不覆盖全班代码、打满内存、长时计算或对话叠加，不能用提高数量上限绕过主机保护。

scratch 必须与交互执行共用主机数量/内存/磁盘保护；scratch 生命周期与 reconcile 遵循同一共享主机锁，取消/超时精确回收自身容器。排队后重新核对会话、授权和 nonce，避免凭排队前的授权执行。锁仅协调遵循该协议的进程，共享主机的其他版本需要独立确认。
