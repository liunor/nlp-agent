"""Transactional removal of one user's owned MySQL data.

Workspace content is removed only when the workspace is the user's own and
has no other members or conversations. Shared workspaces are never deleted.
"""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.base import Base
from server.infrastructure.mysql.models import UserModel


def _table(name: str):
    return Base.metadata.tables[f"nlp_{name}"]


async def purge_user_data(session: AsyncSession, user: UserModel) -> None:
    from .service import HardDeleteBlockedError

    uid = user.id
    members = _table("workspace_members")
    workspaces = _table("workspaces")
    conversations = _table("conversations")
    turns = _table("turns")
    workspace_ids = tuple((await session.scalars(
        select(members.c.workspace_id).where(members.c.user_id == uid)
    )).all())
    active_turn = await session.scalar(
        select(turns.c.id).where(turns.c.user_id == uid,
                                 turns.c.status.in_(("accepted", "running"))).limit(1)
    )
    if active_turn:
        raise HardDeleteBlockedError("Wait for the user's active conversation to finish")

    personal_id = await session.scalar(
        select(workspaces.c.id)
        .join(members, members.c.workspace_id == workspaces.c.id)
        .where(workspaces.c.slug == f"user-{user.username}",
               members.c.user_id == uid, members.c.member_type == "owner")
        .with_for_update()
    )
    conflicting_slug = await session.scalar(
        select(workspaces.c.id).where(workspaces.c.slug == f"user-{user.username}")
    )
    if conflicting_slug is not None and personal_id is None:
        raise HardDeleteBlockedError("Personal workspace ownership cannot be verified")
    if personal_id is not None:
        other_member = await session.scalar(
            select(members.c.user_id).where(
                members.c.workspace_id == personal_id, members.c.user_id != uid
            ).limit(1)
        )
        other_conversation = await session.scalar(
            select(conversations.c.id).where(
                conversations.c.workspace_id == personal_id,
                conversations.c.owner_user_id != uid,
            ).limit(1)
        )
        if other_member or other_conversation:
            raise HardDeleteBlockedError("Personal workspace has other users' data")
        classroom = _table("classrooms")
        classroom_members = _table("classroom_members")
        other_student = await session.scalar(
            select(classroom_members.c.user_id)
            .join(classroom, classroom.c.id == classroom_members.c.classroom_id)
            .where(classroom.c.workspace_id == personal_id,
                   classroom_members.c.user_id != uid)
            .limit(1)
        )
        if other_student:
            raise HardDeleteBlockedError("Personal workspace has other users' classroom data")
        book_files = _table("knowledge_book_files")
        other_book_file = await session.scalar(
            select(book_files.c.id).where(
                book_files.c.workspace_id == personal_id,
                book_files.c.created_by != uid,
            ).limit(1)
        )
        if other_book_file:
            raise HardDeleteBlockedError("Personal workspace has other users' files")
        for name, identity in (
            ("turns", "user_id"),
            ("exercise_sessions", "user_id"),
            ("guided_sessions", "user_id"),
            ("memory_documents", "user_id"),
            ("memory_archives", "user_id"),
            ("usage_events", "user_id"),
            ("quota_reservations", "user_id"),
            ("quota_daily_rollups", "user_id"),
            ("sandbox_executions", "owner_user_id"),
            ("sandbox_leases", "user_id"),
            ("agent_checkpoints", "owner_user_id"),
            ("langgraph_checkpoints", "owner_user_id"),
            ("langgraph_checkpoint_blobs", "owner_user_id"),
            ("langgraph_checkpoint_writes", "owner_user_id"),
        ):
            table = _table(name)
            other_user = await session.scalar(
                select(table.c[identity]).where(
                    table.c.workspace_id == personal_id,
                    table.c[identity].is_not(None),
                    table.c[identity] != uid,
                ).limit(1)
            )
            if other_user:
                raise HardDeleteBlockedError("Personal workspace has other users' data")

    # Runtime containers cannot be removed by a database transaction. Fail
    # closed instead of orphaning a still-allocated sandbox on the host.
    environments = _table("sandbox_environments")
    instances = _table("sandbox_runtime_instances")
    live_runtime = await session.scalar(
        select(instances.c.id)
        .join(environments, instances.c.environment_id == environments.c.id)
        .where(environments.c.owner_user_id == uid,
               instances.c.external_runtime_id.is_not(None))
        .limit(1)
    )
    if live_runtime:
        raise HardDeleteBlockedError("Release the user's sandbox runtime before permanent deletion")

    book_files = _table("knowledge_book_files")
    shared_book_file = await session.scalar(
        select(book_files.c.id).where(book_files.c.created_by == uid)
        .where(book_files.c.workspace_id != personal_id if personal_id is not None else True)
        .limit(1)
    )
    if shared_book_file:
        raise HardDeleteBlockedError("Transfer or remove the user's files in shared workspaces first")
    whiteboard = _table("whiteboard_library_items")
    shared_whiteboard = await session.scalar(
        select(whiteboard.c.id).where(whiteboard.c.created_by == uid).limit(1)
    )
    if shared_whiteboard:
        raise HardDeleteBlockedError("Remove the user's shared whiteboard items before permanent deletion")

    other_owned_turn = await session.scalar(
        select(turns.c.id)
        .join(conversations, turns.c.conversation_id == conversations.c.id)
        .where(turns.c.user_id == uid, conversations.c.owner_user_id != uid)
        .limit(1)
    )
    if other_owned_turn:
        raise HardDeleteBlockedError("User's turns in other users' conversations cannot be fully removed")

    session_ids = list((await session.scalars(
        select(conversations.c.id).where(conversations.c.owner_user_id == uid)
    )).all())
    if session_ids:
        other_turn = await session.scalar(
            select(turns.c.id).where(
                turns.c.conversation_id.in_(session_ids), turns.c.user_id != uid
            ).limit(1)
        )
        if other_turn:
            raise HardDeleteBlockedError("User's conversations contain other users' data")
    turn_filter = turns.c.user_id == uid
    if session_ids:
        turn_filter = or_(turn_filter, turns.c.conversation_id.in_(session_ids))
    if personal_id is not None:
        turn_filter = or_(turn_filter, turns.c.workspace_id == personal_id)
    turn_ids = list((await session.scalars(
        select(turns.c.id).where(turn_filter)
    )).all())

    # Quota accounting has logical owner IDs rather than foreign keys.
    usage = _table("usage_events")
    reservations = _table("quota_reservations")
    buckets = _table("quota_buckets")
    owner_scopes = [("user", uid)]
    if personal_id is not None:
        owner_scopes.append(("workspace", personal_id))
    for owner_type, owner_id in owner_scopes:
        grants = _table("quota_grants")
        grant_ids = list((await session.scalars(select(grants.c.id).where(
            grants.c.owner_type == owner_type, grants.c.owner_id == owner_id
        ))).all())
        if grant_ids:
            ledger = _table("quota_ledger_entries")
            await session.execute(delete(ledger).where(ledger.c.grant_id.in_(grant_ids)))
        for name in ("quota_policy_bindings",):
            table = _table(name)
            await session.execute(delete(table).where(table.c.subject_type == owner_type,
                                                      table.c.subject_id == owner_id))
        for name in ("quota_grants", "quota_adjustments", "quota_credit_operations", "quota_alerts"):
            table = _table(name)
            await session.execute(delete(table).where(table.c.owner_type == owner_type,
                                                      table.c.owner_id == owner_id))

    reservation_filter = reservations.c.user_id == uid
    usage_filter = usage.c.user_id == uid
    if personal_id is not None:
        reservation_filter = or_(reservation_filter, reservations.c.workspace_id == personal_id)
        usage_filter = or_(usage_filter, usage.c.workspace_id == personal_id)
    reservation_ids = list((await session.scalars(select(reservations.c.id).where(reservation_filter))).all())
    usage_rows = (await session.execute(
        select(usage.c.id, usage.c.operation_id).where(usage_filter)
    )).all()
    if usage_rows:
        billing = _table("quota_provider_billing")
        await session.execute(delete(billing).where(or_(
            billing.c.matched_usage_event_id.in_([row.id for row in usage_rows]),
            billing.c.operation_id.in_([row.operation_id for row in usage_rows]),
        )))
    await session.execute(delete(usage).where(usage_filter))
    if reservation_ids:
        ledger = _table("quota_ledger_entries")
        await session.execute(delete(ledger).where(ledger.c.reservation_id.in_(reservation_ids)))
        await session.execute(delete(reservations).where(reservations.c.id.in_(reservation_ids)))
    for owner_type, owner_id in owner_scopes:
        bucket_ids = list((await session.scalars(
            select(buckets.c.id).where(buckets.c.owner_type == owner_type, buckets.c.owner_id == owner_id)
        )).all())
        if bucket_ids:
            ledger = _table("quota_ledger_entries")
            await session.execute(delete(ledger).where(ledger.c.bucket_id.in_(bucket_ids)))
            await session.execute(delete(buckets).where(buckets.c.id.in_(bucket_ids)))
    await session.execute(delete(_table("quota_concurrency_locks")).where(
        _table("quota_concurrency_locks").c.user_id == uid
    ))
    preferences = _table("user_preferences")
    await session.execute(delete(preferences).where(preferences.c.user_id == uid))
    for name in ("quota_daily_rollups", "memory_documents", "memory_archives"):
        table = _table(name)
        criterion = table.c.user_id == uid
        if personal_id is not None:
            criterion = or_(criterion, table.c.workspace_id == personal_id)
        await session.execute(delete(table).where(criterion))

    # A conversation owns its transcript, checkpoints, events and tool calls.
    for name in ("exercise_sessions", "guided_sessions"):
        table = _table(name)
        criterion = table.c.user_id == uid
        if personal_id is not None:
            criterion = or_(criterion, table.c.workspace_id == personal_id)
        await session.execute(delete(table).where(criterion))
    if turn_ids:
        messages = _table("conversation_messages")
        await session.execute(delete(messages).where(messages.c.turn_id.in_(turn_ids)))
        for name in ("tool_audits", "dead_letters", "observability_records"):
            table = _table(name)
            await session.execute(delete(table).where(table.c.turn_id.in_(turn_ids)))
        await session.execute(delete(turns).where(turns.c.id.in_(turn_ids)))
    if session_ids:
        for name in ("conversation_transcripts", "observability_records"):
            table = _table(name)
            await session.execute(delete(table).where(table.c.session_id.in_(session_ids)))
    checkpoints = _table("agent_checkpoints")
    checkpoint_filter = checkpoints.c.owner_user_id == uid
    if session_ids:
        checkpoint_filter = or_(checkpoint_filter, checkpoints.c.session_id.in_(session_ids))
    if personal_id is not None:
        checkpoint_filter = or_(checkpoint_filter, checkpoints.c.workspace_id == personal_id)
    await session.execute(delete(checkpoints).where(checkpoint_filter))
    for name in ("langgraph_checkpoint_writes", "langgraph_checkpoint_blobs",
                 "langgraph_checkpoints"):
        table = _table(name)
        predicate = table.c.owner_user_id == uid
        if session_ids:
            predicate = or_(predicate, table.c.thread_id.in_(session_ids))
        if personal_id is not None:
            predicate = or_(predicate, table.c.workspace_id == personal_id)
        await session.execute(delete(table).where(predicate))
    artifacts = _table("sandbox_artifacts")
    executions = _table("sandbox_executions")
    leases = _table("sandbox_leases")
    artifact_locators = tuple((await session.scalars(
        select(artifacts.c.locator).where(artifacts.c.owner_user_id == uid)
    )).all())
    if artifact_locators:
        from configs.settings import settings

        if not settings.NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT.strip():
            raise HardDeleteBlockedError("Configure the sandbox artifact store before deleting its files")
    await session.execute(delete(artifacts).where(artifacts.c.owner_user_id == uid))
    await session.execute(delete(executions).where(executions.c.owner_user_id == uid))
    await session.execute(delete(leases).where(leases.c.user_id == uid))
    await session.execute(delete(instances).where(instances.c.environment_id.in_(
        select(environments.c.id).where(environments.c.owner_user_id == uid)
    )))
    await session.execute(delete(environments).where(environments.c.owner_user_id == uid))

    # Clear indirect references before removing the conversation and workspace.
    outbox = _table("outbox_messages")
    outbox_filter = outbox.c.payload_json["user_id"].as_string() == uid
    if turn_ids:
        outbox_filter = or_(outbox_filter, outbox.c.payload_json["turn_id"].as_string().in_(turn_ids))
    if session_ids:
        outbox_filter = or_(
            outbox_filter,
            outbox.c.payload_json["session_id"].as_string().in_(session_ids),
            outbox.c.payload_json["conversation_id"].as_string().in_(session_ids),
        )
    if personal_id is not None:
        outbox_filter = or_(outbox_filter, outbox.c.payload_json["workspace_id"].as_string() == personal_id)
    outbox_ids = list((await session.scalars(select(outbox.c.id).where(outbox_filter))).all())
    if outbox_ids:
        dead_letters = _table("dead_letters")
        await session.execute(delete(dead_letters).where(dead_letters.c.outbox_id.in_(outbox_ids)))
    await session.execute(delete(outbox).where(outbox_filter))
    if session_ids:
        await session.execute(delete(conversations).where(conversations.c.id.in_(session_ids)))
    if personal_id is not None:
        cursor = _table("memory_cursors")
        await session.execute(delete(cursor).where(cursor.c.scope_key == f"{personal_id}:{uid}"))
        for name in ("teaching_goals", "course_catalogs"):
            table = _table(name)
            await session.execute(delete(table).where(table.c.workspace_id == personal_id))

    cursor = _table("memory_cursors")
    await session.execute(delete(cursor).where(cursor.c.scope_key.like(f"%:{uid}")))
    credit_locks = _table("quota_credit_scope_locks")
    await session.execute(delete(credit_locks).where(credit_locks.c.scope_key.contains(uid)))
    if user.email_normalized:
        codes = _table("auth_codes")
        email_audits = _table("email_send_audits")
        await session.execute(delete(codes).where(codes.c.kind == "email",
                                                 codes.c.subject == user.email_normalized))
        await session.execute(delete(email_audits).where(
            func.lower(email_audits.c.email) == user.email_normalized
        ))
    await session.execute(delete(book_files).where(book_files.c.created_by == uid))

    audit = _table("authorization_audit_logs")
    await session.execute(delete(audit).where(or_(
        audit.c.actor_user_id == uid, audit.c.target_user_id == uid,
        audit.c.resource_id == uid,
    )))
    tool_audits = _table("tool_audits")
    await session.execute(delete(tool_audits).where(tool_audits.c.actor_id == uid))
    # Accounting for other owners must survive; erase the operator identity.
    for name in ("quota_ledger_entries", "quota_usage_archive_batches"):
        table = _table(name)
        await session.execute(update(table).where(table.c.actor_user_id == uid).values(actor_user_id=None))
    for name in ("quota_role_credit_operations", "quota_credit_operations", "quota_adjustments"):
        table = _table(name)
        await session.execute(update(table).where(table.c.actor_user_id == uid).values(actor_user_id="deleted"))
    grants = _table("quota_grants")
    for field in ("created_by", "revoked_by"):
        await session.execute(update(grants).where(grants.c[field] == uid).values({field: "deleted"}))
    role_credits = _table("quota_role_credit_operations")
    for row_id, recipients in (await session.execute(
        select(role_credits.c.id, role_credits.c.recipient_user_ids)
    )).all():
        if uid in recipients:
            await session.execute(update(role_credits).where(role_credits.c.id == row_id).values(
                recipient_user_ids=[item for item in recipients if item != uid]
            ))
    for name in ("pricing_rules", "quota_policies"):
        table = _table(name)
        await session.execute(update(table).where(table.c.created_by == uid).values(created_by="deleted"))
    cancellations = _table("turn_cancellations")
    await session.execute(update(cancellations).where(cancellations.c.requested_by == uid).values(
        requested_by="deleted"
    ))

    # Sessions and WebSocket tickets are cascaded by the user FK. Their
    # workspace FKs are RESTRICT, so the account must go before its workspace.
    await session.execute(delete(UserModel).where(UserModel.id == uid))
    if personal_id is not None:
        await session.execute(delete(workspaces).where(workspaces.c.id == personal_id))
    from .local_cleanup import LocalCleanup, schedule_local_cleanup

    schedule_local_cleanup(session, LocalCleanup(
        user_id=uid,
        workspace_ids=workspace_ids,
        personal_workspace_id=personal_id,
        session_ids=tuple(session_ids),
        artifact_locators=artifact_locators,
    ))
