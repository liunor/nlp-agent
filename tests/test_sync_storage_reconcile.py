"""Keep synchronous usage exact while replacing thirteen DB round trips with one."""
from decimal import Decimal
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, insert, null, select, text

from server.infrastructure.mysql import models
from server.storage.policy import StorageBucket
from server.storage.quota import StorageQuotaExceeded, SyncStorageQuota
import server.storage.quota as quota_module


class LegacyUsage:
    """Frozen pre-optimization oracle; only used in the isolated CI database."""
    def __init__(self, connection, owner):
        self.connection, self.owner_user_id = connection, owner

    def _sum(self, expression, from_sql, where_sql=""):
        value = self.connection.scalar(
            text(f"SELECT COALESCE(SUM({expression}),0) FROM {from_sql} {where_sql}"),
            {"owner": self.owner_user_id})
        return int(value or 0)

    def _reconcile(self) -> tuple[int, int]:
        core = self._sum("OCTET_LENGTH(COALESCE(title,''))+OCTET_LENGTH(id)", "nlp_conversations", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(COALESCE(input_text,''))+OCTET_LENGTH(COALESCE(result_text,''))+OCTET_LENGTH(COALESCE(error_message,''))+OCTET_LENGTH(CAST(COALESCE(learning_state_json,'{}') AS CHAR))", "nlp_turns", "WHERE user_id=:owner")
        core += self._sum("OCTET_LENGTH(COALESCE(m.content,''))", "nlp_conversation_messages m JOIN nlp_conversations c ON c.id=m.conversation_id", "WHERE c.owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(e.payload_json,'{}') AS CHAR))", "nlp_turn_events e JOIN nlp_turns t ON t.id=e.turn_id", "WHERE t.user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(t.content_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(t.tool_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(t.usage_json,'{}') AS CHAR))", "nlp_conversation_transcripts t JOIN nlp_conversations c ON c.id=t.session_id", "WHERE c.owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(content_json,'{}') AS CHAR))", "nlp_memory_documents", "WHERE user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(payload_json,'{}') AS CHAR))", "nlp_memory_archives", "WHERE user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(checkpoint_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(metadata_json,'{}') AS CHAR))", "nlp_agent_checkpoints", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(checkpoint_blob)+OCTET_LENGTH(metadata_blob)", "nlp_langgraph_checkpoints", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(value_blob)", "nlp_langgraph_checkpoint_blobs", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(value_blob)", "nlp_langgraph_checkpoint_writes", "WHERE owner_user_id=:owner")
        core += self._sum(
            "OCTET_LENGTH(CAST(COALESCE(tc.request_json,'') AS CHAR))"
            "+OCTET_LENGTH(CAST(COALESCE(tc.result_json,'') AS CHAR))",
            "nlp_tool_calls tc JOIN nlp_turns t ON t.id=tc.turn_id",
            "WHERE t.user_id=:owner",
        )
        core += quota_module._uploaded_image_bytes(self.owner_user_id)
        files = self.connection.execute(
            text("SELECT COALESCE(SUM(size_bytes),0) FROM nlp_user_files WHERE owner_user_id=:owner AND kind='file' AND status IN ('active','trashed')"),
            {"owner": self.owner_user_id},
        ).scalar()
        return core, int(files or 0)



def test_usage_uses_one_bound_query_and_adds_uploaded_images():
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.return_value = {
        "core_bytes": Decimal(137), "files_bytes": Decimal(29)}
    owner = "owner-'\\-boundary"
    storage = SyncStorageQuota(connection, owner_user_id=owner, roles={"student"})
    with patch.object(quota_module, "_uploaded_image_bytes", return_value=47) as images:
        assert storage._reconcile() == (184, 29)
    connection.execute.assert_called_once()
    query, params = connection.execute.call_args.args
    assert params == {"owner": owner}
    assert owner not in str(query)
    assert str(query).count(":owner") == 13
    images.assert_called_once_with(owner)


@pytest.fixture
def usage_scope(monkeypatch, tmp_path):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("Requires the isolated migrated CI MySQL service")
    engine = create_engine(os.environ["NLP_AGENT_DATABASE_URL"].replace("mysql+aiomysql://", "mysql+pymysql://"))
    monkeypatch.setattr(quota_module, "DEFAULT_UPLOADS_ROOT", tmp_path)
    try:
        with engine.connect() as connection, connection.begin():
            workspace = connection.scalar(select(models.WorkspaceModel.id).limit(1))
            assert workspace
            owners = [str(uuid4()), str(uuid4())]
            for owner in owners:
                connection.execute(insert(models.UserModel).values(
                    id=owner, username="usage-"+uuid4().hex[:12], password_hash="isolated-test", display_name="Usage probe"))
            try:
                yield engine, connection, workspace, owners, tmp_path
            finally:
                connection.rollback()
    finally:
        engine.dispose()


def seed_history(connection, workspace, owner, image_root, payload):
    conversation, turn = str(uuid4()), str(uuid4())
    connection.execute(insert(models.ConversationModel).values(
        id=conversation, owner_user_id=owner, workspace_id=workspace, title="中文🌍"+payload))
    connection.execute(insert(models.TurnModel).values(
        id=turn, conversation_id=conversation, user_id=owner, workspace_id=workspace,
        input_text=payload, result_text="回复🌍", error_message=None,
        learning_state_json={"nested": [True, None, "中\n\\"]}, status="completed"))
    connection.execute(insert(models.ConversationMessageModel).values(
        id=str(uuid4()), conversation_id=conversation, turn_id=turn, sequence=1, role="user", content=payload))
    connection.execute(insert(models.TurnEventModel).values(
        id=str(uuid4()), turn_id=turn, sequence=1, event_type="message.delta", payload_json={"delta": payload}))
    connection.execute(insert(models.ConversationTranscriptModel).values(
        id=str(uuid4()), session_id=conversation, message_uuid=str(uuid4()), message_type="assistant", role="assistant",
        content_json={"content": payload}, tool_json=null(), usage_json={"tokens": 7}))
    connection.execute(insert(models.MemoryDocumentModel).values(
        id=str(uuid4()), user_id=owner, workspace_id=workspace, scope="user", document_key="probe", content_json=[payload]))
    connection.execute(insert(models.MemoryArchiveModel).values(
        id=str(uuid4()), user_id=owner, workspace_id=workspace, session_id=conversation, source_id="probe", cursor=1,
        payload_json={"archive": payload}))
    connection.execute(insert(models.AgentCheckpointModel).values(
        id=str(uuid4()), owner_user_id=owner, workspace_id=workspace, session_id=conversation,
        checkpoint_ns="", checkpoint_id="1", checkpoint_json={"state": payload}, metadata_json={}))
    connection.execute(insert(models.LangGraphCheckpointModel).values(
        id=str(uuid4()), thread_id=conversation, owner_user_id=owner, workspace_id=workspace, checkpoint_id="1",
        checkpoint_type="bytes", checkpoint_blob=b"\x00\xff"+payload.encode(), metadata_type="bytes", metadata_blob=b""))
    connection.execute(insert(models.LangGraphCheckpointBlobModel).values(
        id=str(uuid4()), thread_id=conversation, owner_user_id=owner, workspace_id=workspace,
        channel="state", version="1", value_type="bytes", value_blob=b"\x00"+payload.encode()))
    connection.execute(insert(models.LangGraphCheckpointWriteModel).values(
        id=str(uuid4()), thread_id=conversation, owner_user_id=owner, workspace_id=workspace, checkpoint_id="1",
        task_id="probe", write_index=0, channel="state", value_type="bytes", value_blob=payload.encode()))
    connection.execute(insert(models.ToolCallModel).values(
        id=str(uuid4()), turn_id=turn, operation_id="probe", claim_generation=0, tool_name="probe",
        idempotency_key=turn+":probe", request_json={"input": payload}, result_json=null()))
    for kind, status, size in [("file", "active", 17), ("file", "trashed", 31),
                                ("file", "deleted", 1000), ("folder", "active", 2000)]:
        connection.execute(insert(models.UserFileModel).values(
            id=str(uuid4()), owner_user_id=owner, workspace_id=workspace,
            kind=kind, status=status, display_name="probe", size_bytes=size))
    for work, size in [("a", 19), ("b", 28)]:
        folder = image_root/work/owner
        folder.mkdir(parents=True)
        (folder/"image.bin").write_bytes(b"x"*size)
    return conversation


def test_mysql_empty_usage_is_zero_in_one_statement(usage_scope):
    engine, connection, _workspace, owners, _root = usage_scope
    storage = SyncStorageQuota(connection, owner_user_id=owners[0], roles={"student"})
    statements = []
    def capture(_c, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert storage._reconcile() == (0, 0)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1


def test_mysql_combined_usage_matches_legacy_for_all_sources_and_owners(usage_scope):
    engine, connection, workspace, owners, root = usage_scope
    for owner, payload in zip(owners, ["中文\n\\🌍", "foreign"*100]):
        seed_history(connection, workspace, owner, root, payload)
    for owner in owners:
        expected = LegacyUsage(connection, owner)._reconcile()
        assert expected[0] > 47 and expected[1] == 48
        storage = SyncStorageQuota(connection, owner_user_id=owner, roles={"student"})
        statements = []
        def capture(_c, _cursor, statement, _params, _context, _many):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert storage._reconcile() == expected
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert len(statements) == 1
        storage.reconcile()
        ledger = connection.execute(text(
            "SELECT core_used_bytes,files_used_bytes FROM nlp_storage_accounts WHERE owner_user_id=:owner"
        ), {"owner": owner}).one()
        assert tuple(ledger) == expected


def test_mysql_combined_final_measurement_still_rejects_and_rolls_back(usage_scope):
    _engine, connection, workspace, owners, root = usage_scope
    owner = owners[0]
    conversation = seed_history(connection, workspace, owner, root, "quota check")
    storage = SyncStorageQuota(connection, owner_user_id=owner, roles={"student"})
    storage.reconcile()
    before = storage._reconcile()
    connection.execute(text(
        "UPDATE nlp_storage_accounts SET core_quota_override_bytes=:quota WHERE owner_user_id=:owner"
    ), {"quota": before[0]+10, "owner": owner})
    with pytest.raises(StorageQuotaExceeded), connection.begin_nested():
        reservation = storage.reserve(StorageBucket.CORE, 1, resource_type="probe")
        connection.execute(text("UPDATE nlp_conversations SET title=:title WHERE id=:id"),
                           {"title": "x"*255, "id": conversation})
        storage.finalize(reservation, reconcile=True)
    assert storage._reconcile() == before
    assert connection.scalar(text(
        "SELECT core_reserved_bytes FROM nlp_storage_accounts WHERE owner_user_id=:owner"
    ), {"owner": owner}) == 0
    assert connection.scalar(text(
        "SELECT COUNT(*) FROM nlp_storage_reservations WHERE owner_user_id=:owner"
    ), {"owner": owner}) == 0
