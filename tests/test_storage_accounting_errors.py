"""Storage byte and transport regression tests independent of a database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import JSON, LargeBinary, Text, column, select
from sqlalchemy.dialects import mysql

from server.infrastructure.mysql.models import (
    LangGraphCheckpointBlobModel, LangGraphCheckpointModel, LangGraphCheckpointWriteModel,
)
from server.storage.accounting import storage_bytes_expr
from server.storage.controller import _write_error
from server.storage.errors import StorageError, StorageQuotaExceeded, StorageQuotaReason
from server.storage.policy import StorageBucket, StoragePolicy
from server.storage.quota import AsyncStorageQuota, SyncStorageQuota, validate_final_usage


@pytest.fixture
def command_error(monkeypatch):
    # Importing the WebSocket module constructs the lazy telemetry engine.
    # Use a non-listening loopback DSN only when no integration DB is configured;
    # these protocol tests never start a gateway or connect to a database.
    from configs.settings import settings
    if not settings.NLP_AGENT_DATABASE_URL:
        monkeypatch.setattr(settings, 'NLP_AGENT_DATABASE_URL',
                            'mysql+aiomysql://fixture:fixture@127.0.0.1:1/storage_protocol_test')
    from server.web.websocket import _command_error
    return _command_error


@pytest.mark.parametrize('value', [column('payload', LargeBinary), LangGraphCheckpointModel.checkpoint_blob,
                                 LangGraphCheckpointModel.metadata_blob, LangGraphCheckpointBlobModel.value_blob,
                                 LangGraphCheckpointWriteModel.value_blob])
def test_opaque_payloads_are_never_cast_to_a_character_set(value):
    sql = str(select(storage_bytes_expr(value)).compile(dialect=mysql.dialect())).lower()
    assert 'octet_length(' in sql
    assert 'cast(' not in sql


@pytest.mark.parametrize('kind', [JSON, Text])
def test_json_and_text_keep_mysql_serialization_byte_accounting(kind):
    sql = str(select(storage_bytes_expr(column('payload', kind))).compile(dialect=mysql.dialect())).lower()
    assert 'octet_length(cast(payload as char))' in sql
    assert 'coalesce(' in sql


@pytest.mark.parametrize('reason,fragment', [
    (StorageQuotaReason.CORE, '账户通用存储额度不足'),
    (StorageQuotaReason.FILES, '个人文件存储额度不足'),
    (StorageQuotaReason.FILE_SIZE, '单文件大小限制'),
    (StorageQuotaReason.FILE_COUNT, '文件数量'),
    (StorageQuotaReason.GLOBAL_DATA, '服务器账户数据空间'),
    (StorageQuotaReason.GLOBAL_FILES, '服务器个人文件存储空间'),
    (StorageQuotaReason.DISK, '服务器可用存储空间'),
    (StorageQuotaReason.UNKNOWN, '存储操作未完成'),
])
def test_http_and_websocket_explain_the_same_reason_without_private_details(reason, fragment, command_error):
    error = StorageQuotaExceeded('PRIVATE_INTERNAL_MESSAGE owner=private', reason=reason)
    code, message, details = command_error(error)
    http_error = _write_error(error)
    assert code == 'storage_quota_exceeded'
    assert fragment in message
    assert http_error.status_code == 413
    assert http_error.detail == message
    assert details == {'storage_reason': reason.value}
    assert 'PRIVATE_INTERNAL_MESSAGE' not in message
    assert 'private' not in str(details)
    if reason != StorageQuotaReason.CORE:
        assert '账户通用存储' not in message


def test_quota_error_class_keeps_existing_imports_and_private_exception_text():
    from server.storage import quota, service
    assert quota.StorageQuotaExceeded is StorageQuotaExceeded is service.StorageQuotaExceeded
    assert quota.StorageError is StorageError is service.StorageError
    assert str(StorageQuotaExceeded('private diagnostic')) == 'private diagnostic'


@pytest.mark.parametrize('bucket,reason', [(StorageBucket.CORE, StorageQuotaReason.CORE),
                                         (StorageBucket.FILES, StorageQuotaReason.FILES)])
def test_sync_admission_retains_limits_and_marks_the_failed_bucket(bucket, reason):
    quota = object.__new__(SyncStorageQuota)
    quota.policy = StoragePolicy(100, 100, 100, 10)
    quota.owner_user_id = 'private'
    quota.connection = MagicMock()
    quota._lock_global_pool = lambda: None
    quota._account = lambda: {'core_used_bytes': 80, 'core_reserved_bytes': 10,
                              'files_used_bytes': 80, 'files_reserved_bytes': 10}
    with pytest.raises(StorageQuotaExceeded) as caught:
        quota.reserve(bucket, 11, resource_type='test')
    assert caught.value.reason == reason
    quota.connection.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('bucket,amount,reason', [(StorageBucket.CORE, 11, StorageQuotaReason.CORE),
                                                (StorageBucket.FILES, 11, StorageQuotaReason.FILES),
                                                (StorageBucket.FILES, 101, StorageQuotaReason.FILE_SIZE)])
async def test_async_admission_keeps_the_same_limits(bucket, amount, reason):
    quota = object.__new__(AsyncStorageQuota)
    quota.policy = StoragePolicy(100, 100, 100, 10)
    quota.owner_user_id = 'private'
    quota.db = SimpleNamespace(scalar=AsyncMock())
    quota._resolve_policy = AsyncMock()
    quota._lock_global_pool = AsyncMock()
    quota._locked_account = AsyncMock(return_value=SimpleNamespace(
        core_used_bytes=80, core_reserved_bytes=10, files_used_bytes=80, files_reserved_bytes=10))
    with pytest.raises(StorageQuotaExceeded) as caught:
        await quota.reserve(bucket, amount, resource_type='test')
    assert caught.value.reason == reason
    quota.db.scalar.assert_not_called()


@pytest.mark.parametrize('core,files,global_bytes,reason', [(101, 0, 101, StorageQuotaReason.CORE),
                                                        (0, 101, 101, StorageQuotaReason.FILES),
                                                        (50, 50, 1001, StorageQuotaReason.GLOBAL_DATA)])
def test_final_settlement_reports_which_limit_rejected_the_write(core, files, global_bytes, reason):
    with pytest.raises(StorageQuotaExceeded) as caught:
        validate_final_usage(core_used_bytes=core, files_used_bytes=files, core_quota_bytes=100,
                             files_quota_bytes=100, global_used_bytes=global_bytes, global_limit_bytes=1000)
    assert caught.value.reason == reason
