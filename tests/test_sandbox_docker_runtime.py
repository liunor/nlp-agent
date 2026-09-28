from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_docker_runtime_reports_host_headroom_for_capacity_guard(monkeypatch) -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    monkeypatch.setattr("server.sandbox.docker_runtime._available_memory_mb", lambda: 4096.0)
    monkeypatch.setattr(
        "server.sandbox.docker_runtime.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=20 * 1024**3),
    )
    adapter = DockerRuntimeAdapter(
        DockerRuntimeConfig(image="ghcr.io/example/runtime@sha256:" + "a" * 64)
    )

    assert adapter.host_resources() == {"available_memory_mb": 4096.0, "disk_free_gb": 20.0}


def test_docker_runtime_uses_linux_memavailable_for_capacity_guard(monkeypatch) -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    meminfo = "MemFree:        242428 kB\nMemAvailable:  4289836 kB\n"

    def fake_open(_path, _mode="r", *, encoding=None):
        assert encoding == "ascii"
        return StringIO(meminfo)

    monkeypatch.setattr("builtins.open", fake_open)
    adapter = DockerRuntimeAdapter(
        DockerRuntimeConfig(image="ghcr.io/example/runtime@sha256:" + "b" * 64)
    )

    assert adapter.host_resources()["available_memory_mb"] == pytest.approx(4289836 / 1024)


def test_docker_runtime_command_has_no_host_or_network_escape_hatches() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig, DockerRuntimeAdapter

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "a" * 64)).create_command(
        name="nova-warm-python-base-1", claim_nonce="nonce"
    )

    assert command[:3] == ("docker", "run", "--detach")
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--pids-limit") + 1] == "128"
    assert all("docker.sock" not in item for item in command)
    assert "nonce" not in command
    assert "--volume" not in command
    assert "/run/nova:rw,nosuid,nodev,uid=10001,gid=10001,mode=700,size=16m" in command
    assert command[-1] == "registry.example/nova@sha256:" + "a" * 64
    assert "nova.sandbox.managed=local" in command
    assert "nova.sandbox.managed=true" not in command
    assert "nova.sandbox.namespace=local" in command


def test_docker_runtime_rejects_mutable_image_references() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig

    try:
        DockerRuntimeConfig(image="nova-sandbox-runtime:local")
    except ValueError as error:
        assert "digest" in str(error)
    else:
        raise AssertionError("mutable Docker image reference must be rejected")


def test_docker_runtime_accepts_an_immutable_local_image_id_for_ci() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig

    image_id = "sha256:" + "2" * 64
    with pytest.raises(ValueError):
        DockerRuntimeConfig(image=image_id)
    assert DockerRuntimeConfig(image=image_id, allow_local_image_id=True).image == image_id


def test_l1_container_command_is_created_not_started() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "d" * 64)).create_l1_command(name="l1")

    assert command[:2] == ("docker", "create")
    assert "--detach" not in command


def test_production_runtime_uses_gvisor_runsc() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "e" * 64)).create_command(name="runsc", claim_nonce="")

    assert command[command.index("--runtime") + 1] == "runsc"


def test_kernel_readiness_requires_a_running_container_and_kernel_connection_file() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "b" * 64))

    async def exercise() -> bool:
        with patch.object(adapter, "healthy", new=AsyncMock(return_value=True)), patch(
            "server.sandbox.docker_runtime.asyncio.create_subprocess_exec"
        ) as spawn:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"", b"")
            spawn.return_value = process
            return await adapter.kernel_ready("container-id")

    assert asyncio.run(exercise())


def test_execution_protocol_uses_stdin_not_a_docker_command_argument() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "c" * 64))

    assert adapter.execute_command("container-id", timeout_seconds=8, output_limit_bytes=1024) == (
        "docker", "exec", "--interactive", "container-id", "python", "/opt/nova-runtime/nova_runtime.py",
        "execute", "--timeout-seconds", "8", "--output-limit-bytes", "1024",
    )


def test_destroy_fails_closed_when_docker_returns_nonzero() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "f" * 64))

    async def exercise() -> None:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            process = AsyncMock()
            process.returncode = 1
            process.communicate.return_value = (b"", b"container is still running")
            spawn.return_value = process
            with pytest.raises(RuntimeError, match="still running"):
                await adapter.destroy("container-id")

    asyncio.run(exercise())


def test_docker_timeout_reaps_cli_without_waiting_on_inherited_pipes() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "9" * 64))

    async def exercise() -> None:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            process = AsyncMock()
            process.communicate.side_effect = TimeoutError()
            process.kill = MagicMock()
            process.wait = AsyncMock(return_value=None)
            spawn.return_value = process
            with pytest.raises(TimeoutError, match="Docker sandbox command timed out"):
                await adapter.managed_runtime_ids()
            process.kill.assert_called_once_with()
            process.wait.assert_awaited_once_with()

    asyncio.run(exercise())


def test_managed_runtime_listing_preserves_full_container_ids() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "1" * 64))

    async def exercise() -> tuple[set[str], tuple[object, ...]]:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"full-container-id\n", b"")
            spawn.return_value = process
            ids = await adapter.managed_runtime_ids()
            return ids, spawn.call_args.args

    ids, command = asyncio.run(exercise())
    assert ids == {"full-container-id"}
    assert command == (
        "docker", "ps", "--all", "--no-trunc", "--quiet",
        "--filter", "label=nova.sandbox.managed=local",
        "--filter", "label=nova.sandbox.namespace=local",
    )


def test_managers_keep_foreign_namespace_runtimes_out_of_reconcile_listing() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    test_adapter = DockerRuntimeAdapter(
        DockerRuntimeConfig(image="registry.example/nova@sha256:" + "a" * 64, namespace="test")
    )
    production_adapter = DockerRuntimeAdapter(
        DockerRuntimeConfig(image="registry.example/nova@sha256:" + "a" * 64, namespace="prod")
    )

    async def exercise() -> tuple[set[str], set[str], list[tuple[object, ...]]]:
        calls: list[tuple[object, ...]] = []

        def spawn(*command: object, **_kwargs: object) -> AsyncMock:
            calls.append(command)
            process = AsyncMock()
            process.returncode = 0
            namespace = next(value for value in command if isinstance(value, str) and value.startswith("label=nova.sandbox.namespace="))
            process.communicate.return_value = (
                (b"test-runtime\n" if namespace.endswith("=test") else b"prod-runtime\n"),
                b"",
            )
            return process

        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec", side_effect=spawn):
            test_ids, production_ids = await asyncio.gather(
                test_adapter.managed_runtime_ids(), production_adapter.managed_runtime_ids()
            )
        return test_ids, production_ids, calls

    test_ids, production_ids, calls = asyncio.run(exercise())
    assert test_ids == {"test-runtime"}
    assert production_ids == {"prod-runtime"}
    assert {command[-1] for command in calls} == {
        "label=nova.sandbox.namespace=test",
        "label=nova.sandbox.namespace=prod",
    }


def test_managed_runtime_count_includes_all_namespaces_for_host_budget() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(
        DockerRuntimeConfig(image="registry.example/nova@sha256:" + "6" * 64, namespace="test")
    )

    async def exercise() -> tuple[int, tuple[object, ...]]:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"test-id\nprod-id\n", b"")
            spawn.return_value = process
            count = await adapter.host_managed_runtime_count()
            return count, spawn.call_args.args

    count, command = asyncio.run(exercise())
    assert count == 2
    assert command == (
        "docker", "ps", "--all", "--no-trunc", "--quiet",
        "--filter", "label=nova.sandbox.managed",
    )


def test_namespace_must_be_a_lowercase_docker_label_value() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig

    with pytest.raises(ValueError, match="namespace"):
        DockerRuntimeConfig(
            image="registry.example/nova@sha256:" + "7" * 64,
            namespace="Production/Blue",
        )


def test_scratch_timeout_force_removes_the_named_container() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "3" * 64))

    async def exercise() -> tuple[tuple[object, ...], ...]:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            run_process = AsyncMock()
            cleanup_process = AsyncMock()
            run_process.kill = MagicMock()
            run_process.communicate = AsyncMock(side_effect=[TimeoutError(), (b"", b"")])
            cleanup_process.communicate.return_value = (b"", b"")
            spawn.side_effect = [run_process, cleanup_process]
            with pytest.raises(TimeoutError, match="scratch execution timed out"):
                await adapter.run_scratch(source="while True: pass", timeout_seconds=1)
            return tuple(call.args for call in spawn.call_args_list)

    commands = asyncio.run(exercise())
    assert len(commands) == 2
    assert commands[1][:3] == ("docker", "rm", "--force")
    assert commands[1][3].startswith("nova-scratch-")


def test_scratch_sends_json_request_to_the_container_stdin() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "4" * 64))

    async def exercise() -> tuple[object, object]:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b'{"status":"completed"}', b"")
            spawn.return_value = process
            await adapter.run_scratch(source="print(1)", timeout_seconds=1)
            return process.communicate.await_args, spawn.call_args

    communicate_call, spawn_call = asyncio.run(exercise())
    assert communicate_call is not None
    assert communicate_call.kwargs["input"] == b'{"source": "print(1)"}'
    assert "--interactive" in spawn_call.args


def test_runtime_usage_returns_only_current_cpu_and_memory_percentages() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "5" * 64))

    async def exercise() -> tuple[dict[str, float], tuple[object, ...]]:
        with patch("server.sandbox.docker_runtime.asyncio.create_subprocess_exec") as spawn:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"12.50%|3.25%\n", b"")
            spawn.return_value = process
            usage = await adapter.usage("container-id")
            return usage, spawn.call_args.args

    usage, command = asyncio.run(exercise())
    assert usage == {"cpu_percent": 12.5, "memory_percent": 3.25}
    assert command == (
        "docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemPerc}}", "container-id"
    )
