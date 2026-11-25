"""Exercise real file ownership SQL and upload/read behavior, without MySQL quota SQL.

The quota seam is covered by existing MySQL integration tests; SQLite here runs
the file workflow without requiring a live deployment.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from io import BytesIO

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from starlette.datastructures import UploadFile

from configs.settings import settings
from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import UserFileModel
from server.storage.service import StorageService, StorageValidationError


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'NLP_AGENT_USER_FILES_ROOT', str(tmp_path / 'files'))
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'files.sqlite'))
    @event.listens_for(engine.sync_engine, 'connect')
    def sqlite_functions(db, _):
        db.create_function('utc_timestamp', 1, lambda _: '2026-11-25 00:00:00')
        db.run_async(lambda connection: connection._execute(
            connection._conn.create_collation, 'ascii_bin', lambda a, b: (a > b) - (a < b)))
    try:
        async with engine.begin() as db:
            await db.run_sync(UserFileModel.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield factory
    finally:
        await engine.dispose()


def user_storage(db, user='alice', workspace='w'):
    service = StorageService(db, AuthenticatedPrincipal(user_id=user, workspace_ids={workspace}, roles={'student'}), workspace)
    service._effective_policy = AsyncMock(return_value=service.scope.policy)
    service.quota = SimpleNamespace(_lock_global_pool=AsyncMock(), _locked_account=AsyncMock(), reserve=AsyncMock(return_value=None),
                                   finalize=AsyncMock(), release=AsyncMock(), reconcile=AsyncMock())
    return service


@pytest.mark.asyncio
async def test_upload_retry_reuses_file_and_repeated_names_are_distinct(file_db):
    async with file_db.begin() as db:
        service = user_storage(db)
        first = await service.create_file(UploadFile(BytesIO(b'first'), filename='report.txt'), upload_key='request-1')
        retry = await service.create_file(UploadFile(BytesIO(b'first'), filename='report.txt'), upload_key='request-1')
        second = await service.create_file(UploadFile(BytesIO(b'second'), filename='report.txt'), upload_key='request-2')
        assert retry['id'] == first['id']
        assert second['id'] != first['id'] and second['name'] != first['name']
        content = await service.read_content(first['id'], expected_hash=first['sha256'])
        assert content['content'] == 'first'
        from server.storage.model_tools import describe_file
        metadata = await describe_file(service, first['id'])
        assert 'expected_hash=' + first['sha256'] in metadata['preview_url']
        assert 'expected_hash=' + first['sha256'] in metadata['download_url']
        with pytest.raises(StorageValidationError):
            await service.create_file(UploadFile(BytesIO(b'changed'), filename='report.txt'), upload_key='request-1')


@pytest.mark.asyncio
async def test_other_owner_workspace_and_trashed_files_cannot_be_read(file_db):
    async with file_db.begin() as db:
        owner = user_storage(db)
        file = await owner.create_file(UploadFile(BytesIO(b'private'), filename='secret.txt'))
        for visitor in [user_storage(db, 'bob'), user_storage(db, workspace='other')]:
            with pytest.raises(StorageValidationError):
                await visitor.read_content(file['id'])
            with pytest.raises(StorageValidationError):
                await visitor.sandbox_path(file['id'])
        await owner.delete(file['id'])
        with pytest.raises(StorageValidationError):
            await owner.read_content(file['id'])


@pytest.mark.asyncio
async def test_generation_publication_keeps_one_original_and_never_overwrites(file_db):
    from server.storage.generated import publish_generated_file
    async with file_db.begin() as db:
        service = user_storage(db)
        pending = await service.create_file(UploadFile(BytesIO(b'complete'), filename='.生成中-task.txt'), origin='generation_pending')
        receipt = dict(sha256=pending['sha256'], size_bytes=8)
        identifier = await publish_generated_file(service, pending['id'], 'result.txt', receipt)
        assert identifier == pending['id']
        assert (await service.get_file(identifier)).origin == 'generated'
        assert (await service.read_content(identifier))['content'] == 'complete'

        # Replaying the successful publication retains the same original file.
        assert await publish_generated_file(service, identifier, 'result.txt', receipt) == identifier
        conflict = await service.create_file(UploadFile(BytesIO(b'other'), filename='.生成中-other.txt'), origin='generation_pending')
        with pytest.raises(StorageValidationError, match='未覆盖'):
            await publish_generated_file(service, conflict['id'], 'result.txt', dict(sha256=conflict['sha256'], size_bytes=5))
        assert (await service.read_content(identifier))['content'] == 'complete'


@pytest.mark.asyncio
async def test_completed_generation_lookup_reuses_saved_file_without_new_reservation(file_db):
    from server.storage.generated import publish_generated_file, completed_generation
    async with file_db.begin() as db:
        service = user_storage(db)
        pending = await service.create_file(UploadFile(BytesIO(b'done'), filename='.生成中-full.txt'), origin='generation_pending')
        key = 'generation:request-full'
        identifier = await publish_generated_file(service, pending['id'], 'full.txt',
                            dict(sha256=pending['sha256'], size_bytes=4), generation_key=key)
        service.quota.reserve.reset_mock()
        assert (await completed_generation(service, key)).id == identifier
        service.quota.reserve.assert_not_awaited()
        assert await completed_generation(user_storage(db, 'bob'), key) is None
