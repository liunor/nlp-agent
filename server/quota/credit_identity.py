"""Resolve quota credit recipients without storing mutable public identities."""

from sqlalchemy import select

from core.public_identity import normalize_public_identity_id
from server.infrastructure.mysql.models import UserModel


async def resolve_credit_user_id(session_factory, identifier: str) -> str | None:
    """Accept an internal ID or a public identity ID for an active user.

    Quota operations must always persist the stable internal ID, so a later
    change of public identity cannot move the recipient's credit history.
    """
    identifier = identifier.strip()
    async with session_factory() as session:
        base = (UserModel.status == "active", UserModel.deleted_at.is_(None))
        internal = await session.scalar(
            select(UserModel.id).where(UserModel.id == identifier, *base)
        )
        if internal is not None:
            return internal
        identity = normalize_public_identity_id(identifier)
        if not identity:
            return None
        return await session.scalar(
            select(UserModel.id).where(UserModel.identity_id == identity, *base)
        )
