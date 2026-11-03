"""Real MySQL -> FUSE -> gVisor read and read-only rejection on Linux CI."""

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SANDBOX_FILES_BRIDGE_INTEGRATION") != "1",
    reason="requires an isolated Linux MySQL/Docker/FUSE test host",
)


async def test_real_personal_file_is_readable_and_readonly_in_runsc(monkeypatch, tmp_path):
    import asyncio
    from sqlalchemy import select
    from configs.settings import settings
    from core.observability.runtime import global_telemetry
    from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
    from server.infrastructure.mysql.models import SandboxLeaseModel, UserFileModel
    from server.rbac.service import rbac_service
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
    from server.sandbox.manager import WarmPoolManager
    from server.sandbox.service import sandbox_lifecycle_service
    from server.user.schemas import UserCreate
    from server.user.service import UserService
    from server.web.database_auth import DatabaseSessionAuth

    monkeypatch.setenv("SANDBOX_ENABLE_FILES_BRIDGE", "true")
    monkeypatch.setenv("SANDBOX_BRIDGE_BASE_DIR", str(tmp_path / "bridge"))
    monkeypatch.setattr(settings, "NLP_AGENT_USER_FILES_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_LOCK_FILE", str(tmp_path / "host.lock"))
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_MEMORY_RESERVE_MB", 256)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_DISK_RESERVE_GB", 1)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_RUNTIME_TOTAL_MAX", 16)
    engine = create_engine(DatabaseConfig(os.environ["NLP_AGENT_DATABASE_URL"], pool_size=2, max_overflow=0))
    factory = create_session_factory(engine)
    image = os.environ["NLP_AGENT_SANDBOX_MANAGER_TEST_IMAGE"]
    process = await asyncio.create_subprocess_exec("docker", "image", "inspect", image,
        "--format", "{{.Id}}", stdout=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    assert process.returncode == 0
    namespace = "files-bridge-" + uuid4().hex[:10]
    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image=stdout.decode().strip(),
        allow_local_image_id=True, namespace=namespace))
    manager = WarmPoolManager(session_factory=factory, docker=adapter,
        resource_profile_id=namespace, ready_target=0, require_host_lock=True)
    claim = None
    try:
        async with factory.begin() as session:
            user = await UserService(session).create_user(UserCreate(username="files" + uuid4().hex[:10],
                display_name="Files bridge test", password="InitialPw0rd1"))
        auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
        _, claims = await auth.login(factory, user.username, "InitialPw0rd1", client_key=namespace)
        async with factory.begin() as session:
            principal = await rbac_service.principal_for_user_id(session, user.id)
            scope = SandboxScope.from_authenticated_request(principal, claims)
            await sandbox_lifecycle_service.ensure_current_lease(session, scope)
            lease = await session.scalar(select(SandboxLeaseModel).where(
                SandboxLeaseModel.auth_session_id == claims.session_id,
                SandboxLeaseModel.workspace_id == scope.workspace_id))
            file_id = str(uuid4())
            key = f"{scope.workspace_id}/{user.id}/{file_id}"
            content = b"id,name\n1,Alice\n"
            physical = tmp_path / "storage" / key
            physical.parent.mkdir(parents=True)
            physical.write_bytes(content)
            session.add(UserFileModel(id=file_id, owner_user_id=user.id,
                workspace_id=scope.workspace_id, parent_id=None, kind="file", display_name="data.csv",
                storage_key=key, size_bytes=len(content), status="active"))
        claim = await manager.claim(scope, lease_id=lease.id)
        assert claim is not None, "real FUSE mount and container provisioning must succeed"
        assert await adapter.files_mount_matches(claim.runtime.external_runtime_id,
            manager._files_mounts.path_for(scope.owner_user_id, scope.workspace_id))
        result = await manager.execute_claimed(scope, lease_id=lease.id, runtime_id=claim.runtime.id,
            generation=claim.runtime.generation, nonce=claim.nonce,
            source="print(open('/files/data.csv').read())\n"
                   "try:\n open('/files/data.csv', 'w').write('wrong')\n"
                   "except OSError as error:\n print('READONLY', error.errno)\n")
        assert result["status"] == "completed", result
        assert "Alice" in result["stdout"]
        # The sandbox uid is not the file's owner, so Unix mode checks may
        # reject the write with EACCES before the read-only mount returns EROFS.
        assert any(f"READONLY {code}" in result["stdout"] for code in (13, 30))
        assert physical.read_bytes() == content
        reused = await manager.claim(scope, lease_id=lease.id)
        assert reused.runtime.id == claim.runtime.id
        assert reused.nonce != claim.nonce
        await manager.destroy_runtime(claim.runtime.id, reason="test.complete", refill=False)
        await manager._reap_files_mounts()
        assert not manager._files_mounts.has_mounts
    finally:
        for external_id in await adapter.managed_runtime_ids():
            await adapter.destroy(external_id)
        await manager.close()
        await global_telemetry.close()
        await engine.dispose()
