from __future__ import annotations

from pathlib import Path


def test_sandbox_runtime_base_image_is_pinned_to_an_immutable_digest() -> None:
    dockerfile = Path("sandbox-runtime/Dockerfile").read_text(encoding="utf-8")
    first_line = dockerfile.splitlines()[0]
    assert first_line.startswith("FROM python:3.11-slim-bookworm@sha256:")
    assert len(first_line.rsplit("@sha256:", 1)[1]) == 64
