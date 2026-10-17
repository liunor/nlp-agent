# T07 复审修复与验证记录

日期：2026-10-17。状态：已完成干净旧基点同环境对比，并修正 34 项测试契约问题；当前全量 939 passed / 56 failed / 4 errors，真实 MySQL 与完整故障/负载验收仍阻塞，不满足 push/PR 的完成前提。

以下旧结果均为历史快照；当前代码与验证状态以“2026-10-17 干净基点对比与全量门禁收敛”为准。历史通过结果不代表本轮代码在真实环境复验通过。

## 2026-10-17 干净基点对比与全量门禁收敛

代码提交 `a533640`；比较起点 `dd8afe5`。本轮只有六个测试文件改动，没有改生产逻辑或部署容量，没有删除/放宽原有业务断言，也没有新增 skip。

- 在全新 detached worktree 冻结 `8e83a72`，避免使用已有脏 baseline。同一共享 venv、显式 localhost 测试占位 DSN、`RUN_API_HTTP=0`，执行默认全量 pytest：**800 passed / 89 failed / 4 errors / 118 skipped**。
- 按完整 case ID 比对，前轮 90 failed + 4 errors 中 92 项在旧基点同样失败；两个 T07 新增夹具问题已修复。32 项旧测试契约问题也完成修正，原有业务断言保留，相关文件 **69 passed**。
- `a533640` 实际默认全量：**939 passed / 56 failed / 4 errors / 130 skipped / 15 warnings**，退出 1，耗时 268.57 秒。剩余 60 项全部在旧基点重现；没有旧基点通过而当前失败的 case。当前共 33 项旧基点失败已通过（本轮修复 32 项，另 1 项会话删除串行化在前轮已通过）。
- 剩余 55 项直接被 MySQL 连接失败阻断；4 项 migration setup 单独读取子进程 stderr，根因也是连接 MySQL 失败；1 项 HTTP 503 单独重放定位到观测库删除 OperationalError，只隔离该外部依赖后原测试通过。最后一项差分仅用于诊断，没有将隔离加入正式全量以刷绿。依赖恢复后仍须复测所有业务断言，不能保证它们一定通过。
- 本轮 Ruff 全目录、修改测试 compileall、diff whitespace 检查通过。没有执行前端复跑，也没有将历史 346/183 数字充当本轮验证。

独立审查范围 `dd8afe5...a533640`：Standards 硬性违规 0、判断性建议 1（两个局部测试 factory/维护补丁重复，非阻断）；Spec 确认新增缺陷/规格外功能 0，但真实集成与交付要求仍未完成。测试协作者隔离只覆盖 HTTP/CRUD 合同，不证明真实额度、触发器或维护 SQL 正确。

完整复现命令、旧基点对比以及前轮 94 项失败/错误的逐项处置见 [基线与收敛报告](t07-gate-baseline-comparison-20261017.md)。

### 环境阻塞与交付边界

最新只读复核 Docker Linux engine 管道不存在（前次为 15 秒超时）；MySQL/Redis 3306/6379 未连接。未启动/重启服务。`gh auth status` 最新复核退出 1，凭据无效。需要用户恢复本机 Docker 和 GitHub 登录，或提供明确隔离、允许测试写入/清理的 MySQL/Redis 环境及可发布的本机认证。不要在聊天中发送 token。

真实 revision 54 迁移/触发器/回滚、四项额度集成用例、Worker 接管、Redis pause/强杀/DB 延迟、额度耗尽、50/30 baseline/final 及容量矩阵均未通过验收。**没有 push、创建 PR、merge 或 tag；须满足用户“做好后再 push 到 develop PR”的前提后再发布。** Compose 保持 1×1。

## 2026-10-17 调用前所有权与超时复审修复

基点：`e2b2d75`。仅修复本轮 P1/P2 及同链路的未派发 Intent 计量边界，不修改部署容量，不启动 Docker/MySQL，不推送远端。

- `DurableQuotaUsageReporter.prepare()` 保持 Turn → 全局容量 → Reservation → Account 锁顺序，按数据库当前时间核验 generation、owner、有效 lease、本地 fence 和 running 状态。Intent 锁等待之后再次核验租约/fence，失权不得插入 Intent 或增加 pending。实际流式/非流式 Provider 派发前再检查本地 fence 与绑定该 fence 的保守、单次派发期限；期限以数据库剩余租约和查询前 monotonic 锚点生成，查询/COMMIT 等待不能延长授权。覆盖准备提交与 telemetry await 之间的本地撤销，以及 COMMIT 跨过租约期限而 heartbeat 尚未撤销的窗口。该授权点检查不等于外部 Provider exactly-once。
- 已发生费用的 `report()`/journal replay 不依赖执行 fence，旧 generation 的迟到用量继续幂等入账。
- 准备 SQL/锁等待默认独立 5 秒期限；超时事务回滚，runtime 分类为 `usage_preparation_timeout`，不经过 Provider 重试/熔断。期限在 COMMIT 前退出，避免主动取消 COMMIT 的不确定性；COMMIT 等待同样不占用 Provider 期限。
- 流式 total/TTFT 起点移到准备成功之后。确定尚未派发的请求使用 `estimated` 零用量与 `provider_not_dispatched` 结束 Intent，不产生 unknown_usage；已开始 Provider 迭代的请求仍保守记录可能发生的费用。

本轮红绿回归：原代码首先出现 **14 failed / 13 passed**，包含旧所有权仍进入 Provider、准备等待吃掉流式期限及缺少独立期限；随后额外的未派发超时、准备后 fence 撤销、准备超时分类均先失败再修复。

本轮已执行：

- 最终代码受影响后端集合重新运行：**183 passed / 6 warnings**。其中 quota reporting **34 项**，覆盖 generation/owner/lease/missing/revoked/wrong-turn、锁等待期间失权、COMMIT 跨租约、流式/非流式零派发、迟到入账、独立准备超时及不误触 Provider 熔断。COMMIT 跨租约的两项用例也先失败再修复，没有复用前轮 161 的数字。
- Ruff、修改文件 compileall、diff whitespace 检查：通过。
- 真实 MySQL 回归文件：**4 tests collected，未执行**。新增真实行锁/generation 接管后旧 Worker 流式/非流式零派发、旧已发生费用仍可上报；既有取消/迟到/提交失败重放用例补齐真实执行所有权。
- 最终代码 `54a51f4` 默认全量后端：**905 passed / 90 failed / 130 skipped / 4 errors / 7 warnings**，退出码 1，耗时 271.60 秒。`RUN_API_HTTP=0`，真实 HTTP 套件按其现有 opt-in 规则不启动，不把 skip 当验证通过。JUnit 实际错误中，55 项是 MySQL 连接拒绝，31 项是缺少 `begin` 的测试替身/调用契约，另外包含 coroutine 不可迭代、Redis heartbeat 断言、Settings 字段及 503 断言等；4 项 setup error 是 Alembic 子进程失败。未将这些全部归因于环境，也未证明它们全部是旧基点失败。完整命令、94 项失败/错误名称和摘要见 [全量门禁清单](t07-dispatch-full-gate-20261017.md)。
- 独立双轴代码审查范围为 `e2b2d75...54a51f4`，包含 `a9e2847` 和 `54a51f4`。Standards：硬性违规 0、判断性 smell 0；Spec：确认缺陷 0、规格外行为 0。审查发现的 COMMIT 跨租约窗口已在后一个提交补红绿回归并修复；静态结论不能替代真实 MySQL 验收。

仍未验证：真实 MySQL revision 54、RR/行锁/事务回滚、真实 Worker 接管、Redis 暂停/强杀/数据库延迟与负载矩阵。前端没有修改，本轮未重跑前轮 346 项及 typecheck/build，不将旧数字当本轮结果。整个 T07 仍不满足部署条件。

## 2026-10-17 六项 P1 复审修复

基点：`cab9e2b`。范围是本轮明确列出的票据接线、对账并发、关闭排空、待补报事实、迟到用量和每次模型调用的预算保护。

1. `StudentSocket` 第三个参数保留默认 ticket provider；第四个 onReady 依次刷新身份、恢复当前历史。Socket 等待恢复成功后才订阅/重放/发送排队命令；关闭或替换的连接不能晚到后恢复。额外修正同一链路的 `RealtimeRequestError` 类型传递和错误展示。流式状态事件直接更新本地消息，避免同时刷新旧 HTTP 快照覆盖新文本；重连与 gap 仍恢复历史。
2. quota reconciler 将候选读取、逐笔释放和余额汇总分成独立短事务。释放与 Reporter 使用 Turn → 全局容量锁 → Reservation → Account；汇总先取得同一全局容量锁，再用 `FOR UPDATE` 当前读 Reservation 明细，最后锁账户修正，避免 REPEATABLE READ 旧快照覆盖新接单预留。
3. Worker 关闭先排空 worker、engine 和 usage replay，最后卸载 Reporter；关闭调用者被取消时仍等待这一排空过程。Provider 完成/取消时的报告保持可用。
4. 新增 revision `20261017_54_t07`：`nlp_model_usage_intents` 和 Reservation 的 `pending_invocations`。每次 Provider attempt 前持久化 Intent、检查/追加预算；未完成计量的 Intent 阻止终态触发器和 reconciler 释放额度。精确结果先写 fsync 后原子发布的 `.data/model-usage-outbox`，再入 MySQL；DB 事务失败保留文件，Worker 启动/周期/排空后重放，operation ID 幂等。DB 已提交但本地文件删除失败时保留冗余文件重放，不使成功 Turn 因清理失败转为失败。
5. 迟到已知 usage 同步更新 `used_tokens` 和 `settled_tokens`；最后一笔 pending 报告完成后结算终态 Turn。`source=none` 保留预算和待对账指标，不伪造零成本。只剩 Intent、没有精确返回事实的异常调用仍需人工/Provider 对账。
6. 接单按 coordinator 的完整上下文上限加最大输出保守预留；后续每次 retry/fallback/工具调用在 Provider 前检查该次模型预算。已知 usage 从未使用的预留移入 used，避免双重占用。后续调用额度不足保持稳定的 `quota_admission_denied`。测试用 SQLite 仓库同步了接口参数兼容，生产 MySQL 额度路径保持强制保护。

持久性/部署约束：所有 Worker 必须挂载保留共享 `.data` 卷；旧 Worker 必须先排空再执行 revision 54，不能与无 Intent 的旧调用混跑。降级有 pending/unknown_usage Intent 时会拒绝执行，避免删除待补报事实。Compose 仍为 1×1。

### 本轮已执行

- 受影响后端测试集合：**161 passed**，包含模型 usage/Provider 合同、quota reporting、admission、Gateway、worker fence/shutdown、迁移图和真实 ASGI TestClient WebSocket 关闭回归；没有再用 `not asyncio` 排除异步用例。连续 100 次 TestClient 连接/订阅失败/ping/断开也通过。
- Windows socketpair 阻塞通过沙箱外测试进程排除：相同 Python asyncio 初始化在沙箱外成功，随后执行上述异步测试。这纠正了前轮将本机异步回归长期视为不可运行的验证边界。
- 前端 typecheck、修改文件 ESLint、production build：通过。前端 unit/component：**55 files / 346 passed**，命令为 `npm test -- --exclude src/platform/http/api.integration.test.ts`，仅明确排除真实后端集成文件。
- Ruff 全后端目录、compileall、迁移图与 revision 53→54 的 offline SQL 生成：通过。唯一 head 为 `20261017_54_t07`。offline SQL 生成不是在 MySQL 执行迁移。
- 新增真实 MySQL 回归 `tests/api_http/chat/test_t07_quota_review_core.py`：**3 tests collected，未执行**。覆盖候选旧快照与接单交错、取消后迟到 usage、report 事务 commit 失败后 Turn failed、保留预留及新 Reporter 重放、重复计量与后续调用额度拒绝。

### 当前门禁与未验证

- 没有启动 Docker 或真实 MySQL；新迁移、RR 行锁语义、触发器 rollback、真实学生浏览器页面连接和三项 MySQL 回归均未验证，不计为通过。
- 全量前端 `npm test` 首次失败包含真实 FastAPI 集成套件选中了缺 SQLAlchemy 的系统 Python。该套件应配置 `PRO_NLP_PYTHON=E:/Github/Pro_NLP/.venv/Scripts/python.exe` 并使用已迁移的隔离 MySQL；本轮没有执行真实后端套件。另两项流式测试暴露的旧 HTTP 快照覆盖实时文本已修复，随后 346 项前端 unit/component 全部通过。
- 全量前端 lint 仍为红：7 errors / 1 warning，位于未修改的 `AccountDialog.tsx`、`FilesPanel.tsx`、`MarkdownContent.tsx`、`ExcalidrawAdapter.tsx`、`WhiteboardLibraryManager.tsx`、`TeacherBookEditor.tsx`。本轮修改文件 lint 通过；未放宽规则或将此项归因于数据库环境。
- 全量后端、冻结旧版本 50/30、故障矩阵、后台持久生命周期及副作用幂等仍需后续完成。此次限定修复和测试结果不足以标记整个 T07 完成或部署就绪。

## 历史记录：2026-10-17 首轮开发侧补齐

在 Docker/真实数据库验收之前，已继续实现以下开发内容：

- Alembic revision `20261016_51_t07` 新增 MySQL 全局 queued/running 与 per-user active 计数器及触发器；默认排队容量 200、用户 active 上限 3。接单、状态迁移、取消/完成和删除通过同一事务内的触发器维护；Worker 启动及周期任务执行计数 reconcile。
- Revision `20261016_52_t07` 新增模型额度账户、Turn Reservation、幂等 Usage Event；reservation 与 Turn/outbox 同事务写入，通过 Redis 任务传播 reservation ID；Reporter 将 Provider usage 按 operation ID 幂等入账；终态触发器释放预留，Worker 周期 reconcile 孤儿/漂移。
- 课堂消息 UI 展示排队位置和服务器等待时间，并在等待期间递增；重连后刷新服务端 Turn 历史，继续显示排队状态并可取消。
- 增加运行时观测接口与仓库指标，包括队列深度/最老等待、待投递 outbox、恢复 handover、额度预留/未知用量，以及同步/异步连接池占用和 Worker heartbeat/槽位。
- MySQL checkpoint 与 transcript 写入现在锁定并核对 Turn 状态、generation、worker owner 和有效 lease。Turn executor 完成或失去 lease 后撤销共享 fence；在 fenced Turn 内拒绝 `join=false` 的临时 Worker，避免它借父 Turn 写入。该限制是安全降级，不等于已实现独立持久 Worker 生命周期。
- 加入 Admission 错误码映射、并发/事务/重试与 quota reconcile 的真实数据库测试，以及 Worker 脱离父 Turn 和排队等待计时的回归用例；这些 MySQL/前端用例尚未在本轮运行。

本轮非 Docker 可执行验证：

- Ruff、Python `compileall`、`git diff --check`：通过。
- `tests/test_t07_admission_codes.py`、Worker 生命周期同步用例及 load safety 同步用例：**14 passed, 1 deselected**。另有无 DSN 的一轮选择性运行出现一个配置导入失败和两个临时目录 setup error，随后通过工作区临时目录和仅供导入的占位 DSN 重跑；不把首次失败计为通过。
- Alembic 图解析：唯一 head 为 `20261016_52_t07`。这不是执行迁移。
- 异步 Worker fence 测试在 Windows 测试运行器创建 asyncio event loop 时卡于系统 socketpair 初始化，已中断；不计作通过。前端测试/类型检查依赖在该隔离工作区不完整，未运行。
- 本轮未启动 Docker、MySQL、Redis、Provider 或真实课堂负载；未生成新的 baseline/final/comparison，也未修改 Compose 扩容配置。

## 历史记录：2026-10-17 复审缺口续修

本轮继续处理已提交开发代码的复审项，没有启动 Docker：

- 额度估算器返回 `None` 时，不再用短输入文本的粗略 token 估算兜底；按 coordinator 可选模型的最大输入上下文加最大输出上限预留。定向回归原先以 260 tokens 通过，修复后预期为 32,256 tokens。
- Worker 执行前从持久 Turn 读取 Reservation ID。旧版 v1 Redis payload 缺少该字段时从数据库补齐；持久 Reservation 缺失或 payload 与 Turn 不匹配时拒绝执行，不能无额度账本运行。
- Gateway `active_turns` 使用数据库运行态计数；`worker_active_slots` 汇总仍有效的 Worker heartbeat。Outbox 提交只代表排队，不再错误计为执行中。
- 新增 revision `20261017_53_t07` 和 ORM 字段：账户 `token_limit`/窗口/`used_tokens`，Reservation `model_profile`/`settled_tokens`/`expires_at`，Usage Event `pricing_version`/`charge_units`。终态与对账会固化实际结算用量。
- 新增 `NLP_AGENT_TEST_MODEL_QUOTA_TOKENS`；只在 `NLP_AGENT_DEPLOYMENT_ENV=test|testing` 为新建或尚未配置额度的账户设默认额度，生产仍可保持无限额度并继续完整记账。
- 新增同步与异步 SQLAlchemy queue pool 的 acquisition 观测：当前 pending checkout、acquire count/sum/max、固定延迟桶和 timeout 计数，并接入运行时 pool 指标；通过无 MySQL 的慢连接/池耗尽测试验证采集逻辑，不代表真实 MySQL 连接池已经运行验收。

本轮可在当前环境运行的验证：

- 最近一次同步回归集合：**50 passed, 24 deselected**。命令包含 admission/helper、迁移图、usage contracts、T07 review regressions、Turn reliability、Worker fencing 和 pool metrics；`-m 'not asyncio'` 明确排除 24 个带 `asyncio` marker 的用例，不等价于完整后端通过。单例 WebSocket 复测的本地 event-loop 阻塞见下项。
- T07 admission/helper、迁移图和 model usage 非异步合同测试：**31 passed, 1 deselected**。
- 旧 v1 payload Reservation 补齐回归：**1 passed**。
- Worker fence/连接池/Compose 预算等同步回归：此前选择集 **7 passed**；该选择集未覆盖本轮新增迁移实际执行。
- Ruff、compileall、迁移图解析和 diff whitespace 检查：通过；当前唯一 Alembic head 为 `20261017_53_t07`。仅图解析，不代表运行了 SQL 迁移。
- `test_fenced_executor_claims_turn_before_invoking_agent` 在 Windows asyncio loop 初始化处超过 30 秒无输出，已中断；尝试 Selector loop 仍然卡住，**未验证，不计通过**。
- 当前再次尝试 `tests/test_web_api.py::test_websocket_reports_deleted_session_subscription_without_crashing` 时，在 `TestClient.__enter__ → anyio.start_blocking_portal → asyncio.new_event_loop → socketpair` 阶段挂住。`-o faulthandler_timeout=10` 的堆栈显示卡在 Windows `socket._fallback_socketpair()` 的 `accept()`，还未进入 WebSocket endpoint；Selector policy 同样卡在 `selector_events._make_self_pipe()`。这是本机测试运行器阻塞，不能当作 `CancelledError` 的新复现，也没有据此改 WebSocket 逻辑。
- `tests/test_mysql_pool_metrics.py`：**3 passed**，覆盖 acquire latency/timeout histogram、并发 pending checkout，以及同步/异步 engine factory 的 pool 接线；不访问 MySQL，已包含在最近一次 50 项通过中。
- 未启动 Docker/MySQL/Redis，没有执行新迁移、DB 并发/事务测试、前端检查或负载测试；没有新的 baseline/final/comparison。

## 后续推进：WebSocket 取消与真实基线

### 已修复并复测

此前偶发 `concurrent.futures.CancelledError` 已通过连续 WebSocket 连接/订阅失败/ping/断开复现。
根因是 transport 的 AnyIO level cancellation 可以打断连接清理；原实现先设置 `_closed`，
随后取消导致清理只完成一部分，再次 close 又因 `_closed` 提前返回。

修复采用连接自有的单一 `_close_task`；并发 close 共同等待该任务，调用者取消不取消清理。
端点在 accept、ready、receive 全阶段共用 finally，并用 AnyIO shield 保护清理。
`wait_closed()` 在资源清理完毕后通知。这里只吸收 ASGI 连接的取消，不吞掉 Turn/Worker 的取消。

回归 `tests/test_t07_websocket_shutdown.py`：

- 连续 100 次真实 TestClient WebSocket 订阅不存在会话、ping 后断开；修改前复现 CancelledError，修改后通过。
- 清理进行中取消调用者，第二次 close 必须等待原清理完成，且调用者仍收到取消。
- accept 阶段取消必须释放 Hub 名额；该测试也先复现失败再修复。

与既有 T07 定向回归合跑：**132 passed, 3 deselected**，三个明确排除项仍为真实 MySQL foundation 测试。
另执行 `pytest tests/test_web_api.py -q --tb=line`：**23 passed, 2 failed**。
失败分别为已确认旧问题 `test_learning_release_notes_route_requests_only_published`
（mock factory 缺 begin），及 `test_http_lifecycle_sessions_chat_settings_and_csrf`
（本次占位 DSN 无真实数据库，删除会话时观测库清理返回 503）。没有跳过它们或改断言。
本次尚未重跑真实 HTTP/MySQL、迁移与完整后端门禁；上文历史结果不可冒充本次结果。
独立只读审查未发现该限定修复的 P1/P2；未审查未验证的 Admission/负载草稿。

### 基线尝试与新环境阻塞

已准备隔离负载启动草稿 `tests/load/run_isolated.py`、真实 Worker/模型 entrypoint，
以及 Admission 并发/事务/429 回归草稿。它们**尚未完成真实验证，也没有 Admission 数据库实现**，
不属于已完成交付。冻结工作区仅复制 tests 工具；旧产品 core/gateway/server/configs/migrations 未修改。

冻结旧版本启动命令：

```powershell
python -m tests.load.run_isolated --run-kind baseline --output artifacts/load-tests/t07/baseline.json --concurrency 1 --replicas 1
```

该命令在 Docker 前置检查失败，没有进入 50/30 负载，因此没有生成 baseline/final/comparison。
当前 Docker Desktop 4.85.0 启动日志明确报告：

```text
starting services: initializing Inference manager:
listening on unix://C:/Users/桂林/AppData/Local/Docker/run/dockerInference:
The file cannot be accessed by the system.
```

`dockerInference` 是零长度 ReparsePoint；WSL `docker-desktop` 为 Stopped，
Docker API 无法响应。已尝试正常启动和 `docker desktop stop --timeout 20` 后重启，
正常停止也失败。未删除/重命名该运行时节点，未修改 Docker 全局设置，未工厂重置或清理卷。
已申请隔离权限尝试把该零长度节点重命名为备份，但系统返回“系统无法访问此文件”，没有发生文件变更；
未进一步删除 reparse point、重置 WSL 或修改 Docker Model Runner 全局设置。
本次真实 Admission 测试为 **3 个 setup errors（Docker info 超时）**，不是测试通过，也没有证明实现行为。

继续真实基线前需恢复 Docker 引擎；涉及运行目录节点或全局配置的修复需用户确认。
Compose 保持 1×1。容量/额度账本/后台生命周期/监控及扩容验收仍按下文未完成清单执行。

## 隔离范围

- 实施分支：`codex/t07-reliability-fixes`。
- 实施工作区：`E:/Github/Pro_NLP/.codex-t07-reliability`。
- 冻结产品基点：`8e83a7296a534504f6f5ef4320046d734412142b`。
- 只读对照工作区：`E:/Github/Pro_NLP/.codex-t07-baseline`，detached HEAD 位于该基点。
- 原工作区的未提交文件没有被还原、暂存或混合提交。本工作区仅隔离 T07 相关文件。
- 未修改前端、RBAC、T06 入班业务或历史迁移；未 push、创建 PR 或部署。

## 六项 P1 对应实现

| 复审问题 | 修复与回归 |
|---|---|
| fencing 未覆盖写入 | `TurnWriteFence` 通过 task/to_thread 传递；SQL UPDATE/INSERT 条件校验 generation、owner、有效 lease 和状态；学习事务结束再校验。失去所有权先 revoke，再取消；不得补写失败或取消。 |
| Worker 整批等待与重复消息清理 | 等待 FIRST_COMPLETED 后补位；按空槽拉取；重复处理器共享原处理结果，不替换取消句柄；清理时比对 task 身份。生产启用 pending reclaim，瞬时失败不会永久搁置已接单任务。 |
| 同步连接池漏算 | Gateway、telemetry、memory、transcript 按进程与 URL/配置共享同步 engine；引用计数释放；显式 size、overflow、timeout。异步和同步池分别预算。 |
| handover 无法解码 | 添加 `GatewayEventType.TURN_HANDOVER`；真实 MySQL 插入恢复事件并从 HTTP 重放。 |
| 短信重发选旧码 | 最新有效 SMS 查询有确定排序；旧码转为 sms_history，不删除限流历史；消费后也保留历史；旧发送延迟完成不能取代新码。 |
| 压测误判通过 | 模式切换必须 200 且回显匹配；每个 Provider 场景必须 202 → failed、错误分类匹配、恰好一个终态、无序列缺口、Session 200；0 个终态和超时均失败。 |

补充修复：

- 终态与必要终态事件同事务提交，避免 HTTP 先读到终态、却缺失终态事件。
- `ensure_event` 锁定 Turn 后按类型定向查询；超过 2000 条事件后仍能去重，并发修复只产生一个事件。
- 取消与完成竞争时保留已提交终态；heartbeat 不得把完成状态改成取消。
- 已持久化取消由 heartbeat 兜底停止执行；过期 lease 不得由旧 heartbeat 复活。
- MySQL DATETIME 使用 naive UTC，避免 aware/naive 比较异常。
- 压测使用 `/auth/session` 轮换后的 CSRF，并在 ws-ticket 写请求中携带 CSRF。
- 长事件流分页读取，并允许旧版本终态事件短暂滞后；分页有截止时间。
- 幂等请求重试先返回既有 Turn，不能被当前 Worker 暂时离线挡住。
- in-process 开发模式发送终态也使用 `ensure_event`，避免与原子终态写入重复。
- baseline 同时校验本地冻结 checkout 和目标服务的产品 commit；旧版 health 只能由 test-only adapter 补元信息。

## 干净基点上的最小前置修复

隔离后暴露出三个基点本身不完整的问题，保留了最小修复，未引入对应功能扩展：

1. 已提交 Gateway 会传 `thinking_enabled`，但 TurnTask 不接受该字段：补任务字段与 codec 往返，执行器只向支持该参数的 engine 传递；没有混入模型配置或 thinking UI。
2. `MySQLSessionService.messages` 引用不存在的 transcript `created_at`：按已有 parent UUID 链排序；没有数据来源的 timestamp 返回 null，不伪造时间、不更改 schema。真实消息读取已覆盖。
3. checkpoint 集成测试使用未导入的 `uuid4`：改为已导入的 `uuid.uuid4`，保证 Ruff 与该真实数据库测试可执行。

## 数据库连接预算

以下只适用于当前 Compose 单 Web、双 Worker、单 monitor、单 sandbox-manager；增加进程/副本必须重算。

| 进程 | 异步池上限 | 共享同步池上限 | 总计 |
|---|---:|---:|---:|
| Web | 5+5 | 5+5 | 20 |
| 每个 Worker（runtime/checkpoint 两个异步 engine） | 2×(2+1) | 4+2 | 12 |
| Monitor | 2+1 | 1+1 | 5 |
| Sandbox manager | 2+2 | 3+3 | 10 |
| 双 Worker 拓扑合计 | | | 59 |
| 迁移/运维预留 | | | 10 |
| 含预留预算 | | | 69 / MySQL max_connections 100 |

pool timeout 为 5 秒。预算是理论上限核算，不是实际 pool-wait SLO 压测结果。
Compose 默认仍为 1 worker × 1 slot，4 槽与双副本仅供隔离环境按阶段验收。

## 已执行验证

使用根工作区已有 `.venv/Scripts/python.exe`，工作目录始终为实施工作区。
HTTP 测试由 `ManagedHttpEnvironment` 创建一次性 MySQL/Redis、执行 `alembic upgrade head`、预置账号并启动 Web/Worker；不访问真实模型或短信 Provider。测试结束只移除本次创建的临时容器。

### 定向回归

以下命令结果：**129 passed, 3 deselected**。这三个明确列出的真实数据库测试随后在临时 MySQL 中单独通过；没有使用 `-k "not mysql"`，文件名含 mysql 的 mock 测试均包含在本轮中。

```powershell
python -m pytest tests/test_t07_review_regressions.py tests/test_sms_resend_state.py tests/test_t07_load_safety.py tests/test_gateway_core.py tests/test_mysql_gateway_repository.py tests/test_mysql_foundation.py tests/test_deployment_settings.py tests/test_redis_transport.py tests/test_worker_fencing.py tests/test_turn_execution.py tests/test_turn_reliability.py tests/test_database_error_mapping.py tests/test_tencent_sms.py -q `
  --deselect=tests/test_mysql_foundation.py::test_alembic_foundation_schema_is_available_in_mysql `
  --deselect=tests/test_mysql_foundation.py::test_mysql_table_comments_are_applied `
  --deselect=tests/test_mysql_foundation.py::test_session_factory_creates_explicit_transactions
```

`tests/test_mysql_foundation.py tests/test_mysql_langgraph_checkpointer.py` 在实际临时 MySQL 上：**14 passed**，包括迁移后表结构、注释、事务与 checkpoint 往返。

### HTTP/WebSocket

```powershell
$env:RUN_API_HTTP = '1'
# 此测试夹具拒绝调用者传入外部 DB/Redis 地址，必须让其自行创建临时容器。
python -m pytest tests/api_http/auth/test_registration_core.py tests/api_http/auth/test_session_lifecycle_core.py tests/api_http/chat/test_chat_worker_async_core.py tests/api_http/chat/test_t07_review_core.py tests/api_http/websocket/test_websocket_async_core.py -q
```

覆盖实际注册与短信重发、会话、运行中取消、Worker 异常、旧 generation SQL 拒绝、handover HTTP 重放、2100 条流事件后并发终态去重、WebSocket 认证与权限重连。
最终执行结果：**27 passed**。

### 静态与迁移图

- `python -m ruff check configs core gateway server tests scripts migrations`：通过。
- `python -m compileall -q configs core gateway server tests/load`：通过。
- `python -m alembic heads`：唯一 head 为 `20261016_50_t06`。
- `tests/test_migration_graph.py`：通过；实际临时 MySQL 已执行迁移至 head。
- `git diff --check`：通过。
- 本轮未修改前端，未运行前端类型检查/测试；全项目验收仍需补跑。

## 全量回归与旧版本对照

首轮占位 DSN 下的 `pytest -q` 出现大量数据库连接失败，不能作为有效集成结果。
随后在真实临时 MySQL 上重新执行完整 `pytest -q`；最终计数和失败清单保存在
`.artifacts/pytest-all-mysql-final.xml`。HTTP 单独结果在 `.artifacts/pytest-http-final.xml`。

全量测试中的 36 个失败已在冻结旧版本、另一套临时 MySQL 中逐一复现，均为相同原因：

最终全量快照：**910 passed, 37 failed, 121 skipped**。除下述 36 项外，
`test_web_api.py::test_websocket_reports_deleted_session_subscription_without_crashing`
本轮全量中偶发 `concurrent.futures.CancelledError`；前一轮全量及随后的单独重跑通过。
该时序失败没有被忽略或标为通过，仍需定位，不计入“环境阻塞”。
冻结旧版本的该单例连续重跑 8 次通过，未获得足以认定根因的证据；不能把它也归为已确认的旧失败。

| 测试 | 数量 | 原因 |
|---|---:|---|
| `test_feedback_api.py` 全部测试 | 29 | mock session factory 是普通 function，没有生产使用的 begin 接口 |
| `test_memory_runtime.py` 两项 | 2 | fixture 的 alice 并非数据库用户，违反 storage_accounts 外键 |
| `test_mysql_master_data.py::test_turn_event_service_delegates_serial_sequence_to_locked_turn_crud` | 1 | quota 查询返回 coroutine，mock 的结果不可迭代 |
| `test_storage_quota_integration.py::test_reconcile_counts_legacy_messages_and_tool_calls` | 1 | fixture 插入消息时 conversation 父记录尚不存在 |
| `test_uploads.py::test_database_authenticated_upload_uses_database_session_dependencies` | 1 | FakeAuthorizationSession 没有 begin 接口 |
| `test_user_management_v3.py::test_password_change_revokes_database_sessions` | 1 | 旧断言仅允许 invalid/expired，实际语义为 authorization changed |
| `test_web_api.py::test_learning_release_notes_route_requests_only_published` | 1 | mock session factory 缺 begin |

对照证据在 `.artifacts/pytest-baseline-existing-failures.xml`（36 failed）；这只是测试缺陷对照，**不是旧版本 50/30 负载 baseline**。
没有删除、跳过或放宽这些失败测试。它们仍使全量 gate 为红；本轮不能宣称全量通过。

## 未完成与未验证

1. Admission 计数、额度 Reservation/Usage ledger、Intent/终态释放与 `20261017_54_t07` 均已实现代码，但本轮没有在真实 MySQL 执行迁移或集成验证；静态检查和单测不能证明 SQL 并发语义、触发器回滚或容量计数无漂移。
2. 后台 Worker 目前在 fenced Turn 中禁止脱离式运行，但尚无独立持久任务表、接单/lease/取消/终态及恢复链路。工具副作用也尚未全面绑定持久 operation ID 与 generation，因此不满足双 Worker 的 exactly-once 启用前提。
3. 指标已提供 pool 使用量、pending checkout、acquire latency 固定桶/最大值/超时，以及队列/执行槽/恢复/额度状态；尚未在真实 MySQL 上验证采集表现，也未补 statement/lock/deadlock 指标和生产监控告警接入。
4. 前端 typecheck、修改文件 lint/build 及 346 项 unit/component 已通过；真实 FastAPI/MySQL 集成和学生浏览器连接未验证。全量前端 lint 仍有范围外 7 errors / 1 warning。
5. 尚无冻结旧版本 50 登录/30 Turn 基线、同参 final/comparison、`1×1 → 1×4 → 2×4`、Worker 强杀、Redis pause、DB delay、慢短信、额度耗尽和 Provider 错误完整故障矩阵结果。
6. 沙箱外执行已恢复 Windows 异步测试，本轮受影响后端 161 项通过；真实 MySQL/HTTP 集成和全量后端尚未运行。历史全量失败快照仍需按当前代码重新采集，不能以定向通过替代。
7. 因上述实现/验证缺口，T07 仍不可部署或扩容。Compose 继续保持 1×1；不得把未运行的迁移、前端或真实负载验证描述为通过。

下一阶段先在可用隔离 MySQL 执行 revision 54 和本轮三项并发/计量回归；再补独立后台任务 lease/取消/恢复与副作用幂等 fence、statement/lock/deadlock 指标及告警，并执行负载与故障验收。在此之前 Compose 保持 1×1。
