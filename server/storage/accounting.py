"""Byte accounting shared by storage admission and usage views."""

from sqlalchemy import LargeBinary, Text, cast, func


def storage_bytes_expr(column):
    """Count opaque bytes directly; only serialize textual/JSON columns as text.

    Casting MsgPack checkpoints to the connection character set can truncate
    invalid UTF-8 or produce NULL. It must never determine a byte quota.
    """
    value = column if isinstance(column.type, LargeBinary) else cast(column, Text)
    return func.coalesce(func.octet_length(value), 0)
