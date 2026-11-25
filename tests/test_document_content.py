from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pytest

from server.documents.content import DocumentError, read_document


def docx_bytes():
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文标题</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>人数</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>结论</w:t></w:r></w:p></w:body></w:document>''')
    return output.getvalue()


@pytest.mark.asyncio
async def test_docx_preserves_paragraph_table_order_and_can_continue(tmp_path):
    path = tmp_path / 'lesson.docx'
    data = docx_bytes()
    path.write_bytes(data)
    first = await read_document(path, name=path.name, expected_hash=sha256(data).hexdigest(), limit=2)
    assert [b['text'] for b in first['blocks']] == ['中文标题', '人数\t42']
    assert first['next_offset'] == 2
    second = await read_document(path, name=path.name, offset=2)
    assert second['blocks'][0]['text'] == '结论'
    assert second['next_offset'] is None


@pytest.mark.asyncio
async def test_text_ranges_and_version_checks(tmp_path):
    path = tmp_path / 'data.csv'
    path.write_bytes('名字,分数\n小明,90\n小红,95'.encode('utf-8-sig'))
    result = await read_document(path, name=path.name, offset=1, limit=1)
    assert result['content'] == '小明,90'
    assert result['next_offset'] == 2
    with pytest.raises(DocumentError) as error:
        await read_document(path, name=path.name, expected_hash='0' * 64)
    assert error.value.code == 'version_changed'


@pytest.mark.asyncio
async def test_xml_entities_are_rejected(tmp_path):
    path = tmp_path / 'bad.docx'
    with ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY a "bad">]><x>&a;</x>')
    with pytest.raises(DocumentError) as error:
        await read_document(path, name=path.name)
    assert error.value.code == 'unsupported_document'


@pytest.mark.asyncio
async def test_pdf_pages_are_located_and_empty_pages_need_ocr(tmp_path):
    from pypdf import PdfWriter
    path = tmp_path / 'scanned.pdf'
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=100, height=100)
    writer.write(path)
    result = await read_document(path, name=path.name)
    assert [b['number'] for b in result['blocks']] == [1, 2, 3]
    assert all(b['needs_ocr'] for b in result['blocks'])
    assert result['next_offset'] == 3


@pytest.mark.asyncio
async def test_single_long_line_can_be_read_without_skipping_its_tail(tmp_path):
    path = tmp_path / 'long.txt'
    path.write_text('前段' * 7000 + '尾部', encoding='utf-8')
    first = await read_document(path, name=path.name)
    assert first['next_offset'] == 0
    second = await read_document(path, name=path.name, offset=first['next_offset'],
                                 char_offset=first['next_char_offset'])
    assert first['content'] + second['content'] == '前段' * 7000 + '尾部'
