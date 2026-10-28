"""Shared terminal accounting for synchronous repositories and fenced Workers."""

import json

from sqlalchemy import text

from server.quota.fixed_turn import FixedTurnQuota, command_for_task


def task_from_state(state):
    from gateway.redis_transport import TurnTaskCodec

    if isinstance(state, str):
        state = json.loads(state)
    payload = state.get("dispatch_task") if isinstance(state, dict) else None
    return TurnTaskCodec.loads(payload) if payload else None


def admit_task(
    connection,
    quota,
    task,
    *,
    user_id,
    workspace_id,
    session_id,
    turn_id,
    role_codes=(),
    classroom_ids=(),
):
    if (
        task is None
        or task.model_snapshot is None
        or task.model_snapshot.billing is None
    ):
        return
    task.model_snapshot.require_owner(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        turn_id=turn_id,
    )
    quota.admit_in_transaction(
        connection,
        command_for_task(task),
        role_codes=role_codes,
        classroom_ids=classroom_ids,
    )


def has_partial_body(connection, turn_id):
    rows = connection.execute(
        text(
            "SELECT payload_json FROM nlp_turn_events WHERE turn_id=:id AND event_type='message.delta'"
        ),
        {"id": turn_id},
    ).all()
    for row in rows:
        payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        if (
            isinstance(payload, dict)
            and not payload.get("background")
            and payload.get("channel") in {None, "content", "final"}
            and isinstance(payload.get("delta"), str)
            and payload["delta"].strip()
        ):
            return True
    return False


def finish_turn(connection, *, turn_id, status, final_text=None, state=None):
    task = task_from_state(state)
    if (
        task is None
        or task.model_snapshot is None
        or task.model_snapshot.billing is None
    ):
        return
    has_body = bool(isinstance(final_text, str) and final_text.strip())
    if status == "cancelled" and not has_body:
        has_body = has_partial_body(connection, turn_id)
    FixedTurnQuota(connection.engine).finish_fixed_in_transaction(
        connection, turn_id=turn_id, status=status, has_body=has_body
    )


async def finish_async(session, turn):
    state = turn.learning_state_json
    if not isinstance(state, dict) or not state.get("dispatch_task"):
        return
    await session.run_sync(
        lambda sync: finish_turn(
            sync.connection(),
            turn_id=turn.id,
            status=turn.status,
            final_text=turn.result_text,
            state=state,
        )
    )
