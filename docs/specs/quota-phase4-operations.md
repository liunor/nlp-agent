# Pro_NLP Phase 4：额度运营、对账与恢复

## 1. 目标与边界

Phase 4 负责把 Phase 1～3 产生的 UsageEvent、Quota Ledger、Grant 和 Bucket 变成可持续运营的读模型、对账流程和恢复工具。Turn 主链路只写事实和必要的 Bucket 状态；Rollup、告警、归档、账单对账和修复均在后台或开发者接口执行。

原始 UsageEvent 和原始 Ledger 永不被对账修复覆盖。对账差异只能通过 `billing_adjustment` 或 `balance_repair` 追加校正记录体现，操作人、原因和幂等键必须保留。

## 2. 数据模型

| 表 | 用途 | 可否作为事实来源 |
| --- | --- | --- |
| `nlp_quota_provider_billing` | Provider 账单明细及本地匹配结果 | Provider 侧输入事实；匹配状态可刷新 |
| `nlp_quota_daily_rollups` | 按日、用户、工作空间、Provider、模型和用途的查询聚合 | 否，随原始 UsageEvent 重建 |
| `nlp_quota_credit_operations` | Gift/Reset 的操作意图和跨请求幂等 | 是，操作记录追加写入 |
| `nlp_quota_credit_scope_locks` | 按幂等键及所有者周期串行化 Gift/Reset 事务 | 否，纯并发控制状态 |
| `nlp_quota_alerts` | 用量突增告警及去重状态 | 是，告警状态可运营更新 |
| `nlp_quota_usage_archive_batches` | UsageEvent 归档批次清单 | 是，归档清单不删除事件 |

账单状态含义：

- `unmatched`：Provider 已有账单，但本地尚未收到对应 UsageEvent；后续相同幂等键重跑会再次查找。
- `pending`：已经找到 UsageEvent，但本地价格或账单 Credits 尚未可用。
- `matched`：本地和 Provider Credits 相等。
- `discrepancy`：两者不同，可定位到 `operation_id` 和 `matched_usage_event_id`，由开发者显式修复。
- `repaired`：已追加校正 Ledger；后续重复对账不会把它重新降级成 discrepancy，也不会重复记账。

## 3. 对账与 pending 闭环

开发者将 Provider 账单批量提交到 `POST /api/v1/developer/quota/billing/reconcile`。服务按 `(provider, operation_id)` 查找 UsageEvent，并以账单明细的幂等键保存输入。账单到达早于本地 UsageEvent 时保留 `unmatched`；延迟 UsageEvent 到达后，重复提交同一行即可刷新为 `matched`、`pending` 或 `discrepancy`。

修复接口为 `POST /api/v1/developer/quota/billing/{billing_id}/repair`。它只读取原始 UsageEvent 和关联 Reservation 的 Bucket，将 Provider 差额写成每个关联 Bucket 的 `billing_adjustment` Ledger 行，并更新物化 consumed/over-limit 状态。原始 `settle`、UsageEvent 和账单原始 JSON 不变。

## 4. Rollup 与异常告警

`build_daily_rollup()` 通过数据库聚合原始 UsageEvent，按 UTC 日和以下维度生成派生行：

`rollup_date + user_id + workspace_id + provider + provider_model + purpose`

Reaper 高频执行 Reservation/Grant 过期扫描；Rollup 和 `detect_usage_anomalies()` 则按 `quota_operations_interval_s`（默认 1 小时）异步重建前一个 UTC 日，避免每个 30 秒 Reaper 周期重复扫描大表。告警默认使用前 7 天日均已计价 Credits 作为基线，目标日超过倍率阈值后写入去重后的 `usage_spike` 告警。该过程不进入 Turn admission/settlement 事务。

## 5. Gift、Reset 与幂等

Gift 和 Reset 都通过 `QuotaCreditOperationModel` 保存操作意图，再委托 Phase 3 Grant 服务创建额度 Grant。相同幂等键、相同请求重试返回第一次操作和 Grant；请求字段变化会被拒绝。Reset 额外把同一所有者、同一 Bucket 周期内的旧 active Grant 标记为 expired，并追加 `grant_reset` Ledger 行。

过期 Grant 由 `QuotaReservationReaper` 调用 `QuotaService.expire_grants()`，该调用仍通过既有快照通知链路，不删除 Grant 或 Ledger。

## 6. Ledger 重放与余额修复

`GET /api/v1/developer/quota/buckets/{bucket_id}/replay` 汇总该 Bucket 的 `reserve/settle/reconcile/release/billing_adjustment` Ledger delta，得到 expected consumed/reserved；over-limit 同时考虑当前有效 Grant、Adjustment 和策略的有限透支，并报告是否与 Bucket 物化值漂移。

`POST /api/v1/developer/quota/buckets/{bucket_id}/repair` 在 Bucket 行锁内把物化值恢复到重放结果，然后追加一条 `balance_repair` Ledger。修复使用 `balance-repair:{idempotency_key}` 幂等；原始流水不改写，因此可以审计修复前后的差异。

## 7. UsageEvent 归档与分区策略

归档接口只给旧 UsageEvent 写入 `archived_at` 和 `archive_batch_id`，并记录 `QuotaUsageArchiveBatchModel`。当前实现不物理删除，避免影响对账、审计和 Ledger 重放；当没有新的事件可归档时直接返回，不创建空批次。

归档标记写入后，个人用量、Daily Rollup、课堂聚合等日常运营查询必须过滤 `archived_at IS NULL`，因此归档事件不会继续出现在活动图和运营统计中；对账、审计和余额恢复查询仍可读取原始事件。确认完成留存期后，开发者可显式执行清理：只删除已归档、已结算且没有 Provider 账单引用的事件，Ledger 与归档批次清单仍保留；系统不会在普通归档请求中自动物理删除。

生产 MySQL 建议按 `occurred_at` 做月度 RANGE 分区，例如 `p202608` 覆盖 `[2026-08-01, 2026-09-01)`。新增月份前先建立下一分区，归档完成且经过账单对账、审计留存确认后，才允许运维导出并删除/交换历史分区。应用提供 `partition_strategy()` 生成分区计划，但物理 `ALTER TABLE` 必须由经过审批的数据库变更执行，不能由请求接口自动 Drop。

## 8. 教师班级聚合

`GET /api/v1/teacher/quota/classroom` 只允许明确拥有该课堂范围权限的非管理员教师访问，并校验课堂属于请求的工作区；随后按 active classroom members 聚合原始 UsageEvent，返回学生数、事件数、已计价 Credits、Token 总量以及 pending/unavailable 状态。告警可通过 `PATCH /api/v1/developer/quota/alerts/{alert_id}` 标记为 `acknowledged` 或 `resolved`，操作保留 RBAC 审计。前端“班级用量”页中的展示值是观察数据，不等价于可用额度，不会把多个 Bucket 的 remaining 相加。

## 9. 运维验收清单

- 先执行 Phase 4 Alembic migration，再启动启用了 Quota 的 Web/Worker。
- 校验 Reaper 正常运行；它同时负责 Reservation/Grant 过期和上一日 Rollup/告警。
- Provider 账单先在测试环境提交 unmatched、pending、matched、discrepancy 四类样例，再验证延迟补齐和 repaired 重跑。
- 随机抽取 Bucket 执行 replay；只有校验结果确认漂移后才执行 repair。
- 真实 MySQL 环境验证 JSON、唯一约束、UTC DATETIME、月度分区和并发幂等；SQLite 测试不能替代该验收。
