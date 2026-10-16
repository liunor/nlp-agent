from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from server.api.v1.persistence.crud import TeachingCrud, TurnCrud
from server.api.v1.persistence.schema import AppendTurnEventCommand, CourseCatalogCommand
from server.api.v1.persistence.service import RevisionConflictError, TeachingService, TurnEventService
from server.infrastructure.mysql.models import CourseCatalogModel, TurnModel


@pytest.mark.asyncio
async def test_catalog_write_rejects_stale_revision() -> None:
    session = AsyncMock()
    crud = AsyncMock(spec=TeachingCrud)
    crud.lock_catalog.return_value = CourseCatalogModel(workspace_id="workspace-1", revision=3)
    service = TeachingService(crud)

    with pytest.raises(RevisionConflictError, match="current revision is 3"):
        await service.replace_catalog(session, CourseCatalogCommand(workspace_id="workspace-1", expected_revision=2))

    crud.replace_catalog.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_write_creates_a_new_version_after_matching_revision() -> None:
    session = AsyncMock()
    catalog = CourseCatalogModel(workspace_id="workspace-1", revision=2)
    crud = AsyncMock(spec=TeachingCrud)
    crud.lock_catalog.return_value = catalog
    service = TeachingService(crud)
    command = CourseCatalogCommand(workspace_id="workspace-1", expected_revision=2, topics=[{"id": "topic-1"}])

    result = await service.replace_catalog(session, command)

    crud.replace_catalog.assert_awaited_once()
    assert result.revision == 2


@pytest.mark.asyncio
async def test_turn_event_service_delegates_serial_sequence_to_locked_turn_crud(monkeypatch) -> None:
    session = AsyncMock()
    turn = TurnModel(id="turn-1", conversation_id="conversation-1", workspace_id="workspace-1", user_id="user-1", input_text="hello", claim_generation=4)
    crud = AsyncMock(spec=TurnCrud)
    crud.lock_turn.return_value = turn
    crud.append_event.return_value = object()

    class NoopQuota:
        def __init__(self, *_args, **_kwargs):
            pass

        async def reserve(self, *_args, **_kwargs):
            return object()

        async def finalize(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("server.api.v1.persistence.service.AsyncStorageQuota", NoopQuota)
    service = TurnEventService(crud)

    await service.append_event(session, AppendTurnEventCommand(turn_id="turn-1", claim_generation=4, event_type="turn.handover"))

    crud.append_event.assert_awaited_once_with(session, turn, generation=4, event_type="turn.handover", payload={})


@pytest.mark.asyncio
async def test_turn_event_service_rejects_a_stale_generation_before_allocating_sequence() -> None:
    session = AsyncMock()
    turn = TurnModel(id="turn-1", conversation_id="conversation-1", workspace_id="workspace-1", user_id="user-1", input_text="hello", claim_generation=5)
    crud = AsyncMock(spec=TurnCrud)
    crud.lock_turn.return_value = turn

    with pytest.raises(RuntimeError, match="fencing generation"):
        await TurnEventService(crud).append_event(session, AppendTurnEventCommand(turn_id="turn-1", claim_generation=4, event_type="message.delta"))

    crud.append_event.assert_not_awaited()
