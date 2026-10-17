# T07 develop 集成交付记录（2026-10-17）

状态：供审查的 Draft PR，不是部署批准。

## 1082e76 复审：取消、终态幂等、进程内所有权

- 已复现：accepted/running 取消调用因 `ensure_event(_connection=...)` 不兼容而抛 TypeError；长流的已有终态被前 2000 条扫描遗漏；MySQL 进程内执行没有 fence 而失败。
- `ensure_event` 现在复用调用方连接，在 Turn 行锁内执行按事件类型的锁定查找与插入；保留 expected generation 与 owner fencing。取消状态、取消命令、取消事件同事务提交，补写失败整体回滚。
- MySQL 进程内模式复用 FencedTurnExecutor 的 claim、续租、授权复核、durable reservation 和本地失权保护；没有放宽 Provider 门禁。显式注入 repository 时从相同 DSN 建立异步运行时。
- 本地定向 143 项通过，Ruff 与 diff 检查通过；SQLite 事务回归仅适配 SQL 方言，不冒充 MySQL 行锁并发验收。
- 真实 HTTP 新增 accepted/running 取消和 MySQL 进程内 prepare→dispatch→usage→settlement 回归，已有 2100 条事件并发补写回归保留。
- PR HTTP marker 从 smoke 扩展为 `api_smoke or api_async_core`。真实异步检查结果以本轮 CI 为准，不沿用 1082e76 的 smoke 全绿结论。
- 后台独立生命周期、副作用幂等、50/30 负载、双 Worker 故障矩阵与完整课堂观察仍不在已验证范围。
- 扩展异步检查进一步发现完成事件提交空窗，现终态与对应事件同事务提交；前台 transcript 在有 fence 时等待有界持久化完成，不能在终态后继续借用失效 fence。
- 修正隔离 HTTP 测试进程缓存的数据库 DSN、Nullable 排队字段断言、超时分类和短信 TTL；未排除任何新增异步用例。
- 短信历史与旧 `(kind,subject)` 唯一键冲突已在实际模型约束下复现。新增 head `20261017_55_t07_sms_history`，只对有效身份保留唯一保护，发送中/历史记录允许多条；不会删除限流历史。降级遇到重复历史会先报错，需管理员归档后操作。
- 所有真实并发/迟到用量/进程内结算回归以最新扩展 CI 的结果为准，不能继续沿用 revision 54 与 smoke 全绿作为验收结论。

## PR #252 首轮 CI 修复

- 旧 revision 02 原先读取实时 TurnModel，导致在额度表创建前声明 T07 外键。现冻结该 revision 的 Turn DDL，额度列/外键仍由 revision 52 创建。
- HTTP 测试收集不再导入会初始化数据库的运行时；选中测试启动后使用隔离环境生成的 DSN。
- MySQL 8.4 开启 binlog 时，非 SUPER 迁移账号创建触发器报 1419。仅对 CI/HTTP 临时 MySQL 启用 `log_bin_trust_function_creators`，应用继续使用普通账号。生产部署前应由 DBA 明确提供触发器迁移权限或配置此变量；应用不会自动扩大数据库权限。
- HTTP 启动器不再对永久 DDL 错误反复执行非事务迁移，以免把原始错误掩盖成重复建表。
- 新增回归先失败后修复；迁移与 CI 回归 21 项通过。真实 CI 结果需要后续单独记录。

## 基线与范围

- 分支：`codex/t07-reliability-develop`，从 `origin/develop` 的 `cbbfb6e` 创建。
- 隔离迁移原 T07 的 10 个提交；未带入原开发分支的 T06 / 文件传输祖先提交。
- 保留 develop 已有 Credits 额度服务、取消协议、邮件认证、知识本和运行时重置逻辑。
- T07 token reservation 与已有 Credits reservation 使用不同字段；持久 reporter 组合已有 reporter，不能互相覆盖或混用 reservation ID。
- T07 迁移接在 develop 的 `20261016_51_t06_phone_identity` 后，单一 head 为 revision 54。
- Compose 继续保持单 Worker、单执行槽，不宣称双 Worker 已验收。

## 本分支实际执行的检查

- 前端 `npm run typecheck`：通过。
- 前端 `npm run lint`：通过。
- 前端 `npm run build`：通过，有大 chunk 提醒。
- 前端 `npm test -- --exclude src/platform/http/api.integration.test.ts`：76 个文件、601 项通过；明确未执行真实 HTTP 集成文件。
- 后端最终扩大范围回归：310 项通过（8 个警告），覆盖额度、Gateway、Worker fencing/取消、模型调用、短信、负载脚本安全和迁移图/完整离线 SQL；使用 `.artifacts/pytest-focused-delivery` 独立临时目录。此前核心六文件的 133 项通过不是最终扩大回归计数。
- `ruff check configs core gateway server tests scripts migrations`、`compileall`、`git diff --check`：通过。
- 全量后端回归：1452 passed、61 failed、295 errors、149 skipped（341 秒），门禁为红。该轮未配置独立 basetemp，存在大量 Windows 共享 pytest 临时目录 WinError 5；运行期间又完成了最后两处定向修复，因此这不是最终提交的完整全量认证。独立目录下最终定向 310 项通过，不能替代全量重跑，也不能把其余失败一律归为旧问题或数据库不可用。

前端首次沙箱内执行因临时缓存 rename EPERM 没有运行测试；相同命令在沙箱外重跑，得到上述 601 项通过。

## 未验证 / 合并与部署前门槛

- 未在真实 MySQL 执行 revision 54、触发器、回滚、并发额度与 Worker 接管验收。
- 未完成真实 Redis 暂停、Worker 强杀、数据库延迟、取消竞态及课堂负载矩阵。
- 缺少本集成分支的真实旧版本负载 baseline/final/comparison，不得把本分支运行结果命名为修复前基线。
- 后台生命周期、副作用幂等和完整课堂观察仍需逐项审查与验收。
- 其余 T07 验证文档记录原隔离分支的历史验证，不能作为本 develop 集成分支的复跑结果。

本次交付目标是推送隔离的 T07 分支并创建面向 develop 的 Draft PR；不执行合并，不绕过审查或 CI。
