# T07 全量门禁：旧基点对比与收敛

日期：2026-10-17。修复代码：`a533640`；前轮报告基点：`dd8afe5`（实现代码 `54a51f4`）；冻结的 T07 前基点：`8e83a72`。

## 结论

本轮修复 34 项可脱离真实数据库验证的测试契约问题，未修改生产代码、未删除原断言、未放宽断言、未增加 skip。剩余 56 failed / 4 errors 全部在干净旧基点重现，当前失败首因均阻塞在外部 MySQL 依赖；这不证明连接恢复后数据库行为一定正确，也不代表 T07 完成。真实 MySQL、故障与负载验收未完成，因此没有 push 或创建到 develop 的 PR。

| 版本 | passed | failed | errors | skipped | 耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 干净 `8e83a72` | 800 | 89 | 4 | 118 | 267.14s |
| `54a51f4`（前轮记录于 `dd8afe5`） | 905 | 90 | 4 | 130 | 271.60s |
| `a533640` | 939 | 56 | 4 | 130 | 268.57s |

以上均是实际默认全量 `pytest` 运行；三个版本测试数量不同，不能简单用总数相减判断回归，下面按完整 case ID 对齐。skip 是原有 opt-in/条件跳过，不计作通过；未使用 `-k not mysql` 或 `not asyncio` 排除 mock/异步测试。当前全量 15 warnings；相关六文件定向回归 69 passed / 10 warnings；Ruff 全目录通过。

## 基线隔离与复现

现有 `.codex-t07-baseline` 有用户/既有未提交修改，未改动它；新建 `.codex-t07-clean-baseline-8e83a72` detached worktree，冻结 `8e83a72`，仅生成忽略的测试 artifacts。没有复制当前实现或修复到旧基点。两个版本共用根目录 venv、相同显式 DSN 和 opt-in 设置，各用独立 basetemp。

```powershell
$env:NLP_AGENT_DATABASE_URL='mysql+aiomysql://test:test@127.0.0.1/nlp_agent_test'
$env:RUN_API_HTTP='0'
# 分别在干净基点和当前 worktree 执行相同测试选择；artifact 文件名区分版本
& 'E:/Github/Pro_NLP/.venv/Scripts/python.exe' -m pytest -q --tb=line --junitxml=<该版本报告路径> --basetemp=<该版本临时路径>
```

本地证据（当前 worktree 的 `.artifacts` 下）：`t07-clean-baseline-8e83a72.{log,xml}`、`t07-dispatch-full-final.{log,xml}`、`t07-gate-convergence-final.{log,xml}`、`t07-gate-contract-red.log`、`t07-gate-contract-green.log`。沙箱外测试进程用于绕开 Windows socketpair 初始化限制，不改变测试选择或断言。

## 按 case ID 的差分

- 前轮 90 failed + 4 errors 中，92 项同名失败/错误在旧基点重现。
- 两项仅当前版本存在的新增失败都是 T07 测试夹具问题：合法 heartbeat 没有容量字段；在 Pydantic Settings 实例上 monkeypatch 方法。现均修复，Redis/Worker 两文件 29 passed。
- 本轮共修复 34 项：上述两项新增夹具问题，以及 32 项旧测试契约问题。旧基点另有一项会话删除串行化失败，在前轮 T07 代码已通过；合计有 33 项旧基点失败在当前代码通过。
- 剩余 60 项均在旧基点重现；没有“旧基点通过、当前失败”或“旧基点不存在、当前失败”的 case。

六文件修复范围：反馈与公开发布说明测试补全 session factory 的 begin 接口，在明确的后台存储 SQL 协作者处隔离；CRUD 与上传合同测试为 AsyncStorageQuota 提供准确异步替身，新增 finalize/owner/resource_type 断言。真实额度、存储维护、事务/触发器测试保留，仍须在 MySQL 运行。Redis fixture 改为有效 heartbeat 并补无效记录，仍断言只统计 2 个有效 Worker 和 Redis 不可用时 unknown；Worker 测试去除不必要的 Pydantic 方法替换，直接执行 join=false 拒绝逻辑。

## 剩余阻塞的证据与下一步

1. 55 项直接报 `OperationalError: Can't connect to MySQL server on 127.0.0.1`。必须在隔离 MySQL 执行迁移后重新运行，不能把被连接阻断的业务断言视作已测。
2. 4 项 file-transfer setup 错误：单独调用同一迁移 fixture 并读取捕获的子进程 stderr，确认 `alembic upgrade head` 退出 1，根因也是 MySQL OperationalError，失败在连接阶段；并非 revision 54 已执行成功。
3. HTTP lifecycle 的 503：单独重放同一测试，观测到 `global_telemetry.repository.delete_session` 抛 OperationalError；仅将这一个外部数据库删除协作者替换成成功返回时，原生命周期断言全部通过。该差分仅是诊断，没有加入正式测试的全局绕过，也不计作真实 MySQL 验收通过。
4. 最新有界只读探测：Docker Linux engine named pipe 不存在（前次为 15 秒超时），3306/6379 端口探测未连接；未启动/重启 Docker 或其他服务。需要用户恢复 Docker，或提供明确隔离、允许清理的 MySQL/Redis 验收环境。
5. 最新 `gh auth status` 复核退出 1，凭据无效。认证应由用户在本机恢复，不在聊天中发送 token。尚未执行 push、PR、merge、tag。

环境恢复后的顺序：revision 54 真实升级与 rollback/触发器校验 → 本轮四项 quota review 集成测试与全量后端 → 真实 HTTP/前端集成 → Worker 接管、强杀、Redis pause、DB 延迟、取消/完成竞态和额度耗尽 → 冻结旧版本 50 登录/30 Turn baseline 与相同负载 final/comparison → 1×1 → 1×4 → 2×4 门槛验收及完整课堂时段观察。Compose 继续保持 1×1。所有条件满足后再按用户授权 push 并创建 base=develop 的 PR。

## 前轮全部 94 项失败/错误的处置

每个 ID 与 JUnit 原样对齐；“已修复”由本轮默认全量运行判定，非仅静态分析。

- `tests.test_feedback_api::test_submit_feedback_requires_authentication`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_submit_feedback_rejects_missing_csrf`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_guest_cannot_submit_feedback`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_student_submission_is_forwarded_and_serialized`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_whitespace_only_body_is_rejected_before_the_service`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_feedback_endpoints_require_developer_permission`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_student_feedback_history_forwards_message_paging`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_student_can_mark_own_feedback_replies_read`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_developer_feedback_detail_forwards_message_paging`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_guest_cannot_read_feedback_list`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_feedback_endpoints_require_system_scope`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_defaults_are_forwarded`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_forwards_limit_offset_and_search`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?limit=0-limit]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?limit=201-limit]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?offset=-1-offset]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?q=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-q]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_get_feedback_detail_returns_the_thread_payload`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_unknown_thread_maps_lookup_error_to_404_problem`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_mark_feedback_read_forwards_ids_and_returns_ok`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_bulk_mark_feedback_read_forwards_thread_ids`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_bulk_delete_feedback_forwards_thread_ids`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?category=suggestion]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?sort=random]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?status=archived]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?priority=urgent]`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_patch_feedback_returns_full_thread_payload`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_reply_feedback_forwards_body`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_feedback_api::test_delete_feedback_forwards_thread_id`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_mysql_master_data::test_turn_event_service_delegates_serial_sequence_to_locked_turn_crud`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_redis_transport::test_dispatcher_counts_live_worker_heartbeats_and_preserves_unknown_state`：T07 新增夹具问题，本轮通过。
- `tests.test_uploads::test_database_authenticated_upload_uses_database_session_dependencies`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_web_api::test_learning_release_notes_route_requests_only_published`：旧基点复现的测试契约问题，本轮通过。
- `tests.test_worker_lifecycle::test_spawn_worker_refuses_detached_child_inside_a_durable_turn`：T07 新增夹具问题，本轮通过。
- `tests.test_coordinator_runtime::test_model_invocation_records_usage_in_a_model_span`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_coordinator_runtime::test_coordinator_stores_evaluation_batch_labels_on_the_root_trace`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_coordinator_runtime::test_model_invocation_does_not_duplicate_usage_for_model_runtime`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_data_integrity::test_request_transaction_commits_user_write`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_data_integrity::test_username_lower_generated_unique`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_data_integrity::test_disable_user_revokes_sessions`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_data_integrity::test_soft_delete_prevents_self_delete`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_data_integrity::test_soft_delete_filters_user_and_revokes_sessions`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_feedback_threads_are_isolated_per_user`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_concurrent_first_submissions_share_one_thread`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_migrated_schema_carries_feedback_constraints_and_indexes`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_sender_type_check_rejects_values_outside_the_vocabulary`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_marking_read_flips_the_unread_count`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_duplicate_thread_per_user_is_rejected_by_unique_constraint`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_update_feedback_thread_persists_fields_and_returns_full_thread`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_unread_sort_orders_across_pages_before_pagination`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_conditional_mark_read_does_not_regress_newer_read_time`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_reply_and_delete_feedback_thread`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_feedback_mysql::test_bulk_feedback_actions_persist_read_state_and_delete_threads`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_file_transfer_integration::test_concurrent_duplicate_send_reserves_capacity_once`：旧基点复现，仍阻塞；迁移 setup 子进程连接 MySQL 失败。
- `tests.test_file_transfer_integration::test_reject_accept_and_expire_settle_the_recipient_ledger`：旧基点复现，仍阻塞；迁移 setup 子进程连接 MySQL 失败。
- `tests.test_file_transfer_integration::test_concurrent_accept_creates_one_received_file`：旧基点复现，仍阻塞；迁移 setup 子进程连接 MySQL 失败。
- `tests.test_file_transfer_integration::test_accept_failure_rolls_back_database_and_removes_destination`：旧基点复现，仍阻塞；迁移 setup 子进程连接 MySQL 失败。
- `tests.test_memory_runtime::test_memory_is_injected_directly_without_a_selector_model`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_memory_runtime::test_runtime_schedules_low_frequency_curator`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_monitor_api::test_monitor_is_admin_only_and_queries_observability`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_monitor_api::test_monitor_live_events_ignores_a_client_that_disconnects_during_heartbeat`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_mysql_foundation::test_alembic_foundation_schema_is_available_in_mysql`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_mysql_foundation::test_mysql_table_comments_are_applied`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_mysql_langgraph_checkpointer::test_mysql_checkpoint_round_trip_and_idempotent_writes`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_observability::test_trace_span_usage_and_queries`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_observability::test_error_aggregation_and_live_subscription`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_observability::test_telemetry_repository_clear_removes_traces_events_and_daily_usage`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_observability::test_observability_queries_are_scoped_to_principal`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_observability::test_runtime_moves_pending_events_to_a_replacement_event_loop`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_rbac_mysql::test_mysql_rbac_assignment_replaces_roles_and_bumps_authorization_version`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_rbac_mysql::test_mysql_rbac_rejects_unknown_role_without_changing_assignments`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_auth_revocation_releases_the_same_session_lease_atomically`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_idle_session_expiry_releases_its_sandbox_lease`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_reconciler_marks_expired_leases_without_a_browser_request`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_users_sharing_a_workspace_receive_distinct_environments`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_disabled_user_revokes_active_sandbox_leases`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_concurrent_lease_claims_create_one_environment`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_sandbox_phase0::test_concurrent_model_scratch_creates_one_environment`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_security_hardening::test_classroom_approve_rejects_cross_class_request_id`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_security_hardening::test_admin_revoke_session_revokes_only_target_user`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_security_hardening::test_self_password_change_verifies_old_and_invalidates_sessions`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_security_hardening::test_admin_create_user_returns_no_password_and_persists`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_storage_quota_integration::test_reconcile_counts_legacy_messages_and_tool_calls`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_storage_quota_integration::test_finalize_rejects_measured_usage_after_write`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_storage_quota_integration::test_concurrent_reservations_cannot_oversubscribe_account`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_storage_quota_integration::test_concurrent_reservations_cannot_oversubscribe_global_pool`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_storage_quota_integration::test_new_file_and_folder_responses_load_server_defaults`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_user_management_v3::test_new_user_is_persisted_with_guest_role`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_user_management_v3::test_concurrent_duplicate_phone_registration_has_one_winner`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_user_management_v3::test_new_user_can_login_case_insensitively`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_user_management_v3::test_database_developer_role_does_not_include_sensitive_data_by_default`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_user_management_v3::test_password_change_revokes_database_sessions`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_user_management_v3::test_websocket_ticket_is_origin_bound_and_single_use`：旧基点复现，仍阻塞；MySQL 连接失败。
- `tests.test_web_api::test_http_lifecycle_sessions_chat_settings_and_csrf`：旧基点复现，仍阻塞；HTTP 503，独立诊断为观测库删除连接失败。

## Standards

独立静态审查 `dd8afe5...a533640`：未发现新增明确规范违规。判断性建议 1：反馈和公开发布说明测试重复了小型 `FakeSessionFactory` 和三个维护函数替换，可考虑共享测试 helper；重复规模较小，非阻断项。本轮保持局部测试边界，没有为此改动生产模块。

## Spec

独立静态审查未发现确认的新增缺陷或规格外功能。六个文件没有删除原行为断言；维护 SQL 不属于这些 HTTP 合同测试的目标，CRUD/上传额度 mock 位于现有协作者边界，并保留/补充 finalize、账户及资源类型断言。Redis 使用真实心跳合同的数据并保留无效记录；Worker 仍从真实路径断言 fenced Turn 拒绝 detached child。

但“全量红灯收敛至通过、真实 MySQL、完整故障验收”仍未完成，旧基点重现不等于门禁通过；当前不应 push 或创建可合并 PR。审查员没有复跑测试，本文件数字来自主执行过程的实际日志/JUnit。

双轴汇总：Standards 硬性违规 0、判断性建议 1（最严重为轻微重复测试支架）；Spec 确认新增实现缺陷 0，集成验收与交付要求仍有未完成项。
