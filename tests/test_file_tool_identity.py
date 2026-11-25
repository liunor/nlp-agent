from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from server.storage.model_tools import authorize_file_context


class Session:
    def __init__(self, *, revoked=False, user='u', workspace='w'):
        self.login = SimpleNamespace(user_id=user, workspace_id=workspace,
            revoked_at=datetime.now(UTC) if revoked else None,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1), authorization_version=1)
        self.user = SimpleNamespace(status='active', deleted_at=None, authorization_version=1)

    async def get(self, model, identifier):
        return self.login if model.__name__ == 'SessionModel' else self.user


@pytest.mark.asyncio
@pytest.mark.parametrize('details', [dict(revoked=True), dict(user='another'), dict(workspace='another')])
async def test_file_tools_reject_invalid_login_before_reading_storage(details):
    config = {'configurable': dict(thread_id='chat', user_id='u', workspace_id='w', auth_session_id='login')}
    with pytest.raises(PermissionError):
        await authorize_file_context(Session(**details), config)


@pytest.mark.asyncio
async def test_file_tools_never_use_conversation_as_login_identity():
    with pytest.raises(PermissionError):
        await authorize_file_context(Session(), {'configurable': dict(thread_id='login', user_id='u', workspace_id='w')})


@pytest.mark.asyncio
async def test_storage_image_preserves_ocr_billing_lifecycle(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from PIL import Image
    import hashlib
    from server.storage import model_tools
    from server.tools.api import image_analyze_tool
    from server.quota import reporting
    image_path = tmp_path / 'image.png'
    Image.new('RGB', (96, 64)).save(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    file = SimpleNamespace(sha256=digest)
    storage = SimpleNamespace(download_path=AsyncMock(return_value=(file, image_path)), get_file=AsyncMock(return_value=file))
    @asynccontextmanager
    async def authorized(_config):
        yield storage
    monkeypatch.setattr(model_tools, '_storage', authorized)
    begin = AsyncMock(return_value='reservation')
    complete = AsyncMock()
    monkeypatch.setattr(reporting, 'begin_billable_tool_usage', begin)
    monkeypatch.setattr(reporting, 'complete_billable_tool_usage', complete)
    monkeypatch.setattr(reporting, 'cancel_billable_tool_usage', AsyncMock())
    class Vision:
        async def analyze(self, request, *, before_provider, resolved_asset):
            assert resolved_asset.reference.sha256 == digest
            await before_provider(resolved_asset, 'ocr', 'ocr')
            return SimpleNamespace(model_dump_json=lambda **kw: '{"ok":true}')
    monkeypatch.setattr(image_analyze_tool, 'build_image_analyze_service', lambda **kw: Vision())
    config = {'configurable': dict(thread_id='chat', user_id='u', workspace_id='w', auth_session_id='login')}
    result = await model_tools.analyze_user_image.ainvoke(dict(file_id='f', question=''), config)
    assert result == '{"ok":true}'
    begin.assert_awaited_once()
    complete.assert_awaited_once_with('reservation')
