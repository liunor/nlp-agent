"""Real FUSE/runsc write acceptance; only touches a unique diagnostic directory."""
import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import select

from configs.settings import settings
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import UserFileModel
from server.rbac.service import rbac_service
from server.storage.files_mounts import StorageMountRegistry
from server.storage.service import StorageService
from server.storage.bridge_writes import BridgeWrites


async def main():
    engine = create_engine(DatabaseConfig.from_runtime(settings.database_runtime))
    factory = create_session_factory(engine)
    registry = StorageMountRegistry(factory)
    prefix = "sandbox-write-acceptance-" + uuid4().hex
    owner = workspace = None
    try:
        async with factory() as session:
            node = await session.scalar(select(UserFileModel).where(
                UserFileModel.kind == "file", UserFileModel.status == "active").limit(1))
            assert node is not None, "Upload a platform file first"
            owner, workspace = node.owner_user_id, node.workspace_id
        mount = await registry.ensure(owner, workspace)
        max_bytes = await BridgeWrites(owner, workspace, factory).max_file_bytes()
        code = '''
from pathlib import Path
import errno, os
root = Path('/files') / PREFIX
root.mkdir()
p = root / '中文.txt'
p.write_text('沙箱生成的结果', encoding='utf-8')
assert p.read_text(encoding='utf-8') == '沙箱生成的结果'
with p.open('a', encoding='utf-8') as f: f.write('追加')
assert p.read_text(encoding='utf-8').endswith('追加')
with p.open('r+b') as f:
    f.seek(0); f.write(b'ABC'); f.truncate(3)
assert p.read_bytes() == b'ABC'
try: os.truncate(p, 10**12)
except OSError as e: assert e.errno in (errno.EDQUOT, errno.EFBIG), e
else: raise AssertionError('Quota was bypassed')
assert p.read_bytes() == b'ABC'
temp = root / 'atomic.tmp'
temp.write_bytes(b'atomic')
os.replace(temp, p)
assert p.read_bytes() == b'atomic'
sub = root / '目录'
sub.mkdir()
p.rename(sub / '重命名.txt')
assert (sub / '重命名.txt').read_bytes() == b'atomic'
(sub / '重命名.txt').unlink()
sub.rmdir()
root.rmdir()
assert not root.exists()
print('CREATE_READ_APPEND_TRUNCATE_QUOTA_REPLACE_MOVE_TRASH_OK')
'''
        process = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--runtime", "runsc", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--user", "10001:10001", "--memory", "128m", "--cpus", "0.25",
            "--ulimit", f"fsize={max_bytes}:{max_bytes}",
            "--mount", f"type=bind,source={mount},target=/files,readonly=false",
            os.environ["BRIDGE_PROBE_IMAGE"], "python", "-B", "-c", "PREFIX=" + repr(prefix) + "\n" + code)
        assert await process.wait() == 0, "runsc write acceptance failed"
        async with factory() as session:
            root = await session.scalar(select(UserFileModel).where(
                UserFileModel.owner_user_id == owner, UserFileModel.workspace_id == workspace,
                UserFileModel.display_name == prefix))
            assert root.status == "trashed"
            sub = await session.scalar(select(UserFileModel).where(UserFileModel.parent_id == root.id,
                UserFileModel.kind == "folder"))
            file = await session.scalar(select(UserFileModel).where(UserFileModel.parent_id == sub.id,
                UserFileModel.kind == "file"))
            assert file.status == "trashed" and file.size_bytes == 6
            assert file.sha256 == hashlib.sha256(b"atomic").hexdigest()
        print(json.dumps({"metadata_size_hash_trash": "passed", "runsc_writes": "passed"}))
        store = BridgeWrites(owner, workspace, factory)
        rollback_path = "/" + prefix + "-rollback"
        await store.run("mkdir", rollback_path)
        await store.run("create", rollback_path + "/data.txt")
        await store.run("write", rollback_path + "/data.txt", data=b"before", offset=0)

        class RollbackFactory:
            def __call__(self):
                return factory()

            @asynccontextmanager
            async def begin(self):
                async with factory.begin() as session:
                    yield session
                    raise RuntimeError("injected.commit.failure")

        try:
            await BridgeWrites(owner, workspace, RollbackFactory()).run(
                "write", rollback_path + "/data.txt", data=b"after!", offset=0)
        except RuntimeError as error:
            assert str(error) == "injected.commit.failure"
        else:
            raise AssertionError("Injected transaction failure did not occur")
        from server.storage.service import storage_path_for, StorageScope
        async with factory() as session:
            item = await store.resolve(session, rollback_path + "/data.txt")
            path = storage_path_for(StorageScope(owner, workspace, None), item.storage_key)
            assert path.read_bytes() == b"before"
        print("TRANSACTION_FAILURE_PRESERVES_ORIGINAL_OK")
    finally:
        await registry.reap(set())
        # Remove only generated fixtures (children before parents), including blobs.
        if owner:
            async with factory.begin() as session:
                principal = await rbac_service.principal_for_user_id(session, owner)
                service = StorageService(session, principal, workspace)
                roots = (await session.scalars(select(UserFileModel).where(
                    UserFileModel.owner_user_id == owner, UserFileModel.workspace_id == workspace,
                    UserFileModel.display_name.in_((prefix, prefix + "-rollback"))))).all()
                for root in roots:
                    async def cleanup(item):
                        children = (await session.scalars(select(UserFileModel).where(
                            UserFileModel.parent_id == item.id))).all()
                        for child in children:
                            await cleanup(child)
                        if item.status == "active":
                            await service.delete(item.id)
                        await service.permanently_delete(item.id)
                    await cleanup(root)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
