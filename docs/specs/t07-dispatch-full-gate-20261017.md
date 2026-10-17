# T07 调用前所有权与超时修复：全量后端门禁

日期：2026-10-17。代码 HEAD：`54a51f4`（基点 `e2b2d75`）。

实际结果：**905 passed / 90 failed / 130 skipped / 4 errors / 7 warnings**，耗时 271.60 秒。退出码 1，不能标记全量通过。

在隔离 worktree 运行（共享根目录 venv，沙箱外进程解决 Windows socketpair 限制）：

```powershell
$env:NLP_AGENT_DATABASE_URL='mysql+aiomysql://test:test@127.0.0.1/nlp_agent_test'
$env:RUN_API_HTTP='0'
& 'E:/Github/Pro_NLP/.venv/Scripts/python.exe' -m pytest -q --tb=line --junitxml=.artifacts/t07-dispatch-full-final.xml --basetemp=.artifacts/pytest-t07-dispatch-full-final
```

没有启动 Docker/MySQL。130 skip 包括现有 opt-in 真实 HTTP 用例及其他原有条件跳过；不是验证通过。完整本地日志在 `.artifacts/t07-dispatch-full-final.log`，JUnit 在 `.artifacts/t07-dispatch-full-final.xml`。

按 JUnit 实际异常：55 项 MySQL 连接拒绝，31 项缺失 begin 的测试替身/调用契约问题；其余还有 coroutine 不可迭代、Redis heartbeat 断言、Settings 字段问题、503 断言以及 4 项 Alembic setup 错误。未把所有失败都归因于环境，也未隔离运行旧基点证明它们都是旧失败。

## 全部失败/错误名称

每项保留实际错误摘要；多行长错误取第一行。

- `FAIL tests.test_coordinator_runtime::test_model_invocation_records_usage_in_a_model_span` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_coordinator_runtime::test_coordinator_stores_evaluation_batch_labels_on_the_root_trace` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_coordinator_runtime::test_model_invocation_does_not_duplicate_usage_for_model_runtime` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_data_integrity::test_request_transaction_commits_user_write` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_data_integrity::test_username_lower_generated_unique` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_data_integrity::test_disable_user_revokes_sessions` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_data_integrity::test_soft_delete_prevents_self_delete` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_data_integrity::test_soft_delete_filters_user_and_revokes_sessions` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_api::test_submit_feedback_requires_authentication` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_submit_feedback_rejects_missing_csrf` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_guest_cannot_submit_feedback` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_student_submission_is_forwarded_and_serialized` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_whitespace_only_body_is_rejected_before_the_service` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_feedback_endpoints_require_developer_permission` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_student_feedback_history_forwards_message_paging` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_student_can_mark_own_feedback_replies_read` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_developer_feedback_detail_forwards_message_paging` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_guest_cannot_read_feedback_list` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_feedback_endpoints_require_system_scope` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_defaults_are_forwarded` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_forwards_limit_offset_and_search` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?limit=0-limit]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?limit=201-limit]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?offset=-1-offset]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_out_of_range_query_params[?q=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-q]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_get_feedback_detail_returns_the_thread_payload` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_unknown_thread_maps_lookup_error_to_404_problem` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_mark_feedback_read_forwards_ids_and_returns_ok` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_bulk_mark_feedback_read_forwards_thread_ids` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_bulk_delete_feedback_forwards_thread_ids` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?category=suggestion]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?sort=random]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?status=archived]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_list_feedback_rejects_invalid_enum_query_params[?priority=urgent]` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_patch_feedback_returns_full_thread_payload` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_reply_feedback_forwards_body` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_api::test_delete_feedback_forwards_thread_id` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_feedback_mysql::test_feedback_threads_are_isolated_per_user` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_concurrent_first_submissions_share_one_thread` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_migrated_schema_carries_feedback_constraints_and_indexes` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_sender_type_check_rejects_values_outside_the_vocabulary` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_marking_read_flips_the_unread_count` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_duplicate_thread_per_user_is_rejected_by_unique_constraint` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_update_feedback_thread_persists_fields_and_returns_full_thread` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_unread_sort_orders_across_pages_before_pagination` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_conditional_mark_read_does_not_regress_newer_read_time` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_reply_and_delete_feedback_thread` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_feedback_mysql::test_bulk_feedback_actions_persist_read_state_and_delete_threads` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `ERROR tests.test_file_transfer_integration::test_concurrent_duplicate_send_reserves_capacity_once` — failed on setup with "subprocess.CalledProcessError: Command '['E:\\Github\\Pro_NLP\\.venv\\Scripts\\python.exe', '-m', 'alembic', 'upgrade', 'head']' returned non-zero exit status 1."
- `ERROR tests.test_file_transfer_integration::test_reject_accept_and_expire_settle_the_recipient_ledger` — failed on setup with "subprocess.CalledProcessError: Command '['E:\\Github\\Pro_NLP\\.venv\\Scripts\\python.exe', '-m', 'alembic', 'upgrade', 'head']' returned non-zero exit status 1."
- `ERROR tests.test_file_transfer_integration::test_concurrent_accept_creates_one_received_file` — failed on setup with "subprocess.CalledProcessError: Command '['E:\\Github\\Pro_NLP\\.venv\\Scripts\\python.exe', '-m', 'alembic', 'upgrade', 'head']' returned non-zero exit status 1."
- `ERROR tests.test_file_transfer_integration::test_accept_failure_rolls_back_database_and_removes_destination` — failed on setup with "subprocess.CalledProcessError: Command '['E:\\Github\\Pro_NLP\\.venv\\Scripts\\python.exe', '-m', 'alembic', 'upgrade', 'head']' returned non-zero exit status 1."
- `FAIL tests.test_memory_runtime::test_memory_is_injected_directly_without_a_selector_model` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_memory_runtime::test_runtime_schedules_low_frequency_curator` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_monitor_api::test_monitor_is_admin_only_and_queries_observability` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_monitor_api::test_monitor_live_events_ignores_a_client_that_disconnects_during_heartbeat` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_mysql_foundation::test_alembic_foundation_schema_is_available_in_mysql` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_mysql_foundation::test_mysql_table_comments_are_applied` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_mysql_langgraph_checkpointer::test_mysql_checkpoint_round_trip_and_idempotent_writes` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_mysql_master_data::test_turn_event_service_delegates_serial_sequence_to_locked_turn_crud` — TypeError: 'coroutine' object is not iterable
- `FAIL tests.test_observability::test_trace_span_usage_and_queries` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_observability::test_error_aggregation_and_live_subscription` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_observability::test_telemetry_repository_clear_removes_traces_events_and_daily_usage` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_observability::test_observability_queries_are_scoped_to_principal` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_observability::test_runtime_moves_pending_events_to_a_replacement_event_loop` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] 由于目标计算机积极拒绝，无法连接。)")
- `FAIL tests.test_rbac_mysql::test_mysql_rbac_assignment_replaces_roles_and_bumps_authorization_version` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_rbac_mysql::test_mysql_rbac_rejects_unknown_role_without_changing_assignments` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_redis_transport::test_dispatcher_counts_live_worker_heartbeats_and_preserves_unknown_state` — assert 0 == 2
- `FAIL tests.test_sandbox_phase0::test_auth_revocation_releases_the_same_session_lease_atomically` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_sandbox_phase0::test_idle_session_expiry_releases_its_sandbox_lease` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_sandbox_phase0::test_reconciler_marks_expired_leases_without_a_browser_request` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_sandbox_phase0::test_users_sharing_a_workspace_receive_distinct_environments` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_sandbox_phase0::test_disabled_user_revokes_active_sandbox_leases` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_sandbox_phase0::test_concurrent_lease_claims_create_one_environment` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_sandbox_phase0::test_concurrent_model_scratch_creates_one_environment` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_security_hardening::test_classroom_approve_rejects_cross_class_request_id` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_security_hardening::test_admin_revoke_session_revokes_only_target_user` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_security_hardening::test_self_password_change_verifies_old_and_invalidates_sessions` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_security_hardening::test_admin_create_user_returns_no_password_and_persists` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_storage_quota_integration::test_reconcile_counts_legacy_messages_and_tool_calls` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_storage_quota_integration::test_finalize_rejects_measured_usage_after_write` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_storage_quota_integration::test_concurrent_reservations_cannot_oversubscribe_account` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_storage_quota_integration::test_concurrent_reservations_cannot_oversubscribe_global_pool` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_storage_quota_integration::test_new_file_and_folder_responses_load_server_defaults` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_uploads::test_database_authenticated_upload_uses_database_session_dependencies` — AttributeError: 'FakeAuthorizationSession' object has no attribute 'begin'
- `FAIL tests.test_user_management_v3::test_new_user_is_persisted_with_guest_role` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_user_management_v3::test_concurrent_duplicate_phone_registration_has_one_winner` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_user_management_v3::test_new_user_can_login_case_insensitively` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_user_management_v3::test_database_developer_role_does_not_include_sensitive_data_by_default` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_user_management_v3::test_password_change_revokes_database_sessions` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_user_management_v3::test_websocket_ticket_is_origin_bound_and_single_use` — sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '127.0.0.1'")
- `FAIL tests.test_web_api::test_learning_release_notes_route_requests_only_published` — AttributeError: 'function' object has no attribute 'begin'
- `FAIL tests.test_web_api::test_http_lifecycle_sessions_chat_settings_and_csrf` — assert 503 == 204
- `FAIL tests.test_worker_lifecycle::test_spawn_worker_refuses_detached_child_inside_a_durable_turn` — ValueError: "Settings" object has no field "get_agent_runtime"
