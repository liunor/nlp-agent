"""Application services that own revisions and global Turn event ordering."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import ConversationModel, CourseCatalogModel, ExerciseSessionModel, TurnModel
from .crud import ConversationCrud, LearningCrud, TeachingCrud, TurnCrud
from .schema import AppendTurnEventCommand, CourseCatalogCommand, CourseCatalogResult, CreateConversationCommand, CreateExerciseSessionCommand, CreateTurnCommand
from server.storage.policy import StorageBucket
from server.storage.quota import AsyncStorageQuota


class RevisionConflictError(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"course catalog revision conflict; current revision is {current_revision}")


class TeachingService:
    def __init__(self, crud: TeachingCrud | None = None) -> None:
        self._crud = crud or TeachingCrud()

    async def replace_catalog(self, session: AsyncSession, command: CourseCatalogCommand) -> CourseCatalogResult:
        catalog = await self._crud.lock_catalog(session, command.workspace_id)
        if catalog is None:
            if command.expected_revision != 0:
                raise RevisionConflictError(0)
            catalog = CourseCatalogModel(workspace_id=command.workspace_id, revision=0)
            session.add(catalog)
            await session.flush()
        if catalog.revision != command.expected_revision:
            raise RevisionConflictError(catalog.revision)
        await self._crud.replace_catalog(session, catalog, topics=command.topics, blueprints=command.blueprints, summary=command.change_summary)
        return CourseCatalogResult(workspace_id=catalog.workspace_id, revision=catalog.revision, topics=command.topics, blueprints=command.blueprints)


class ConversationService:
    async def create(self, session: AsyncSession, command: CreateConversationCommand) -> ConversationModel:
        quota = AsyncStorageQuota(session, owner_user_id=command.owner_user_id, roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(command.id.encode("utf-8")) + len(command.title.encode("utf-8")) + 256, resource_type="conversation", resource_key=command.id)
        row = await ConversationCrud().create(session, ConversationModel(id=command.id, workspace_id=command.workspace_id, owner_user_id=command.owner_user_id, title=command.title))
        await quota.finalize(reservation, reconcile=True)
        return row


class LearningService:
    async def create_exercise_session(self, session: AsyncSession, command: CreateExerciseSessionCommand) -> ExerciseSessionModel:
        quota = AsyncStorageQuota(session, owner_user_id=command.user_id, roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(json.dumps(command.blueprint_snapshot, ensure_ascii=False, default=str).encode("utf-8")) + 256, resource_type="exercise_session", resource_key=command.id)
        row = await LearningCrud().create_exercise_session(session, ExerciseSessionModel(id=command.id, conversation_id=command.conversation_id, workspace_id=command.workspace_id, user_id=command.user_id, topic_id=command.topic_id, mode=command.mode, blueprint_snapshot_json=command.blueprint_snapshot))
        await quota.finalize(reservation, reconcile=True)
        return row


class TurnEventService:
    def __init__(self, crud: TurnCrud | None = None) -> None:
        self._crud = crud or TurnCrud()

    async def create_turn(self, session: AsyncSession, command: CreateTurnCommand) -> TurnModel:
        state_json = json.dumps(command.learning_state or {}, ensure_ascii=False, default=str)
        quota = AsyncStorageQuota(session, owner_user_id=command.user_id, roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(command.input_text.encode("utf-8")) + len(state_json.encode("utf-8")) + 512, resource_type="turn", resource_key=command.id)
        turn = await self._crud.create(session, TurnModel(id=command.id, conversation_id=command.conversation_id, workspace_id=command.workspace_id, user_id=command.user_id, input_text=command.input_text, idempotency_key=command.idempotency_key, learning_state_json=command.learning_state))
        await quota.finalize(reservation, reconcile=True)
        return turn

    async def append_event(self, session: AsyncSession, command: AppendTurnEventCommand):
        turn = await self._crud.lock_turn(session, command.turn_id)
        if turn is None:
            raise KeyError(command.turn_id)
        if turn.claim_generation != command.claim_generation:
            raise RuntimeError("turn fencing generation no longer matches")
        quota = AsyncStorageQuota(session, owner_user_id=turn.user_id, roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(json.dumps(command.payload, ensure_ascii=False, default=str).encode("utf-8")) + 256, resource_type="turn_event", resource_key=f"{turn.id}:{command.event_type}")
        event = await self._crud.append_event(session, turn, generation=command.claim_generation, event_type=command.event_type, payload=command.payload)
        await quota.finalize(reservation, reconcile=True)
        return event
