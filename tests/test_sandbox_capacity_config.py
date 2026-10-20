"""Runtime settings and host guards must describe the same allocation."""

import asyncio
from pathlib import Path
import shutil
import subprocess

import pytest
from pydantic import ValidationError
import yaml


def test_settings_keep_existing_profile_and_accept_measured_candidate(monkeypatch):
    from configs.settings import Settings

    monkeypatch.delenv("NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB", raising=False)
    monkeypatch.delenv("NLP_AGENT_SANDBOX_DOCKER_CPUS", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB == 768
    assert defaults.NLP_AGENT_SANDBOX_DOCKER_CPUS == 1.0
    candidate = Settings(_env_file=None, NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB=512,
                         NLP_AGENT_SANDBOX_DOCKER_CPUS=1.0)
    assert candidate.NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB == 512


@pytest.mark.parametrize("field,value", [
    ("NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB", 255),
    ("NLP_AGENT_SANDBOX_DOCKER_CPUS", 0),
    ("NLP_AGENT_SANDBOX_DOCKER_CPUS", float("nan")),
    ("NLP_AGENT_SANDBOX_DOCKER_CPUS", float("inf")),
])
def test_invalid_resource_settings_fail_before_runtime_creation(field, value):
    from configs.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize("memory,expected", [
    ("512m", 512), ("768M", 768), ("1g", 1024), ("536870913", 513),
])
def test_memory_guard_rounds_up_actual_docker_cap(memory, expected):
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="nova@sha256:"+"a"*64, memory=memory))
    assert adapter.memory_limit_mb == expected


@pytest.mark.parametrize("memory", ["0", "-1m", "512m --privileged"])
def test_unknown_memory_cap_cannot_silently_use_default_budget(memory):
    from server.sandbox.docker_runtime import DockerRuntimeConfig

    with pytest.raises(ValueError):
        DockerRuntimeConfig(image="nova@sha256:"+"a"*64, memory=memory)


def test_factory_applies_candidate_without_weakening_isolation():
    from server.sandbox.runtime_factory import create_runtime_adapter

    adapter = create_runtime_adapter(backend="runsc", image="nova@sha256:"+"b"*64,
                                      namespace="test", memory_mb=512, cpu_cores=1.0)
    args = adapter.create_l1_command(name="candidate")
    assert args[args.index("--memory")+1] == "512m"
    assert args[args.index("--cpus")+1] == "1.0"
    assert args[args.index("--runtime")+1] == "runsc"
    assert args[args.index("--network")+1] == "none"
    assert "--read-only" in args and "ALL" in args
    assert adapter.memory_limit_mb == 512
    with pytest.raises(ValueError, match="Docker-backed"):
        create_runtime_adapter(backend="kubernetes", image="nova@sha256:"+"b"*64,
                               memory_mb=512)


@pytest.mark.parametrize("disk", [20.0, None])
def test_manager_budgets_actual_profile_and_all_not_yet_created_reservations(monkeypatch, disk):
    from configs.settings import settings
    from server.sandbox.manager import WarmPoolManager

    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_MEMORY_RESERVE_MB", 3072)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_DISK_RESERVE_GB", 15)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX", 4)

    class Docker:
        memory_limit_mb = 512

        def host_resources(self):
            return {"available_memory_mb": 3750.0, "disk_free_gb": disk}

        async def host_managed_runtime_count(self):
            return 0

    manager = WarmPoolManager(session_factory=object(), docker=Docker(),
                              resource_profile_id="python-base", ready_target=2)

    async def exercise():
        # A 512 MiB runtime fits where the former fixed 768 MiB check did not.
        assert await manager._host_allows_create(total_count=0)
        # The first reservation has not reached Docker or MemAvailable yet.
        assert not await manager._host_allows_create(total_count=1, reserved_count=1)

    asyncio.run(exercise())


@pytest.mark.parametrize("source,expected", [
    ('NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX="6"\n', 'NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX="6"'),
    ('NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX=5\n', 'NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX=5'),
    ('OTHER=retained\n', 'NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX="4"'),
])
def test_real_test_release_awk_preserves_reviewed_budget_or_supplies_default(source, expected):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root/".github/workflows/publish-test-image.yml").read_text(encoding="utf-8"))
    deploy = next(step for step in workflow["jobs"]["deploy"]["steps"]
                  if "AUTH_DEPLOY_SOURCE_DIR" in step.get("env", {}))
    program = deploy["run"].split('awk -v nova_image_ref="$NOVA_IMAGE_REF" -v sandbox_runtime_ref="$SANDBOX_RUNTIME_REF" \'', 1)[1].split('\' "$DEPLOY_DIR/.env"', 1)[0]
    awk = shutil.which("awk")
    if awk is None:
        bundled = Path("C:/Program Files/Git/usr/bin/awk.exe")
        if bundled.is_file():
            awk = str(bundled)
    if awk is None:
        pytest.skip("real awk is required; Linux CI provides it")
    source += 'NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB=512\nNLP_AGENT_SANDBOX_DOCKER_CPUS=1.0\n'
    result = subprocess.run([awk, "-v", "nova_image_ref=example@sha256:aaa",
                             "-v", "sandbox_runtime_ref=example@sha256:bbb", program],
                            input=source, text=True, capture_output=True, check=True, timeout=5)
    values = result.stdout.splitlines()
    assert [line for line in values if line.startswith("NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX=")] == [expected]
    assert "NLP_AGENT_SANDBOX_DOCKER_MEMORY_MB=512" in values
    assert "NLP_AGENT_SANDBOX_DOCKER_CPUS=1.0" in values
