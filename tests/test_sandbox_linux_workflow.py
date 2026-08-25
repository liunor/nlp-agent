from __future__ import annotations

from pathlib import Path


def test_linux_smoke_uses_the_registered_gvisor_runtime() -> None:
    workflow = Path(".github/workflows/sandbox-linux.yml").read_text(encoding="utf-8")

    assert "docker info --format '{{json .Runtimes}}'" in workflow
    assert "--runtime runsc" in workflow
