"""Durable local outbox for usage facts awaiting MySQL acknowledgement.

Production workers share the persistent .data volume. A pre-dispatch MySQL
intent protects the reservation even if this volume becomes unwritable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from core.model_runtime.reporters import UsageEventConflictError
from core.model_runtime.usage import CanonicalTokenUsage, InvocationOutcome, ModelInvocation


class UsageJournal:
    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory is not None else (
            Path(__file__).resolve().parents[2] / ".data" / "model-usage-outbox"
        )

    def put(self, invocation: ModelInvocation, usage: CanonicalTokenUsage, outcome: InvocationOutcome) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{invocation.operation_id}.json"
        encoded = json.dumps({
            "invocation": invocation.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        temporary = self.directory / f".{uuid.uuid4()}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Publish an entire fsynced file without replacing an existing
                # operation's first fact (including across Worker processes).
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != encoded:
                    raise UsageEventConflictError(f"Conflicting journal fact: {invocation.operation_id}")
            self._sync_directory()
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def pending(self) -> list[Path]:
        return sorted(self.directory.glob("*.json"))

    @staticmethod
    def read(path: Path) -> tuple[ModelInvocation, CanonicalTokenUsage, InvocationOutcome]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            ModelInvocation.model_validate(data["invocation"]),
            CanonicalTokenUsage.model_validate(data["usage"]),
            InvocationOutcome.model_validate(data["outcome"]),
        )

    def acknowledge(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        self._sync_directory()

    def _sync_directory(self) -> None:
        # Windows does not expose fsync for directories; file contents are
        # fsynced before the atomic hard-link publication on both platforms.
        if os.name != "nt":
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
