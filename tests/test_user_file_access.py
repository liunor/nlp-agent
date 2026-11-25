from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.identity import AuthenticatedPrincipal
from server.storage.service import StorageService, StorageValidationError


def storage(nodes):
    service = StorageService(SimpleNamespace(), AuthenticatedPrincipal(user_id='u', workspace_ids={'w'}), 'w')
    async def get(identifier):
        if identifier not in nodes:
            raise StorageValidationError('文件或文件夹不存在')
        return SimpleNamespace(id=identifier, **nodes[identifier])
    service.get_file = AsyncMock(side_effect=get)
    return service


@pytest.mark.asyncio
async def test_sandbox_path_resolves_authorized_parents_and_unicode_names():
    service = storage({'f': dict(parent_id='p', display_name='成绩 表.csv', kind='file'),
                       'p': dict(parent_id=None, display_name='数据', kind='folder')})
    assert await service.sandbox_path('f') == '/files/数据/成绩 表.csv'
    service.get_file.assert_any_await('p')


@pytest.mark.asyncio
async def test_deleted_or_unavailable_parent_does_not_return_a_sandbox_path():
    service = storage({'f': dict(parent_id='gone', display_name='x.csv', kind='file')})
    with pytest.raises(StorageValidationError):
        await service.sandbox_path('f')


@pytest.mark.asyncio
async def test_cyclic_parent_tree_is_rejected():
    service = storage({'f': dict(parent_id='f', display_name='loop', kind='folder')})
    with pytest.raises(StorageValidationError):
        await service.sandbox_path('f')
