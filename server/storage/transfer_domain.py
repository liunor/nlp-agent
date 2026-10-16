"""Pure file-transfer state and naming rules."""

from __future__ import annotations

from pathlib import Path


class InvalidTransferTransition(ValueError):
    pass


def transition_transfer(status: str, action: str, *, actor: str) -> str:
    transitions = {
        ("pending", "accept", "recipient"): "accepted",
        ("pending", "reject", "recipient"): "rejected",
        ("pending", "cancel", "sender"): "cancelled",
        ("pending", "expire", "system"): "expired",
    }
    try:
        return transitions[(status, action, actor)]
    except KeyError as error:
        raise InvalidTransferTransition("文件发送请求已经处理或当前用户无权执行此操作") from error


def next_available_file_name(original: str, existing_names: set[str]) -> str:
    folded = {name.casefold() for name in existing_names}
    if original.casefold() not in folded:
        return original
    path = Path(original)
    suffix = path.suffix
    stem = original[: -len(suffix)] if suffix else original
    index = 1
    while True:
        candidate = f"{stem} ({index}){suffix}"
        if candidate.casefold() not in folded:
            return candidate
        index += 1
