import pytest
from pydantic import ValidationError

from server.web.contracts import ChatAttachment


def test_storage_reference_and_legacy_image_are_exclusive():
    assert ChatAttachment(file_id='f', expected_hash='a' * 64).file_id == 'f'
    assert ChatAttachment(file_name='image.png').file_name == 'image.png'
    for body in [{}, dict(file_id='f', file_name='image.png')]:
        with pytest.raises(ValidationError):
            ChatAttachment(**body)


def test_file_retry_uses_id_and_version_not_mutable_names_or_paths():
    import json
    from gateway.core import _matches_file_retry
    record = dict(id='f', sha256='a' * 64, name='old name.pdf', sandbox_path='/files/old name.pdf')
    persisted = 'summarize\n\n---附件---\n[文件] ' + json.dumps(record) + '\n---附件结束---'
    assert _matches_file_retry(persisted, 'summarize', [dict(file_id='f', expected_hash='a' * 64)])
    assert not _matches_file_retry(persisted, 'different', [dict(file_id='f')])
    assert not _matches_file_retry(persisted, 'summarize', [dict(file_id='other')])
