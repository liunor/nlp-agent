import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from server.documents.generation import render_file, generation_source


def test_docx_is_a_real_document_with_escaped_text_and_deterministic_bytes():
    first = render_file('docx', '报告\n<结果>&中文')
    assert first == render_file('docx', '报告\n<结果>&中文')
    from io import BytesIO
    with ZipFile(BytesIO(first)) as archive:
        xml = archive.read('word/document.xml').decode()
        assert '&lt;结果&gt;&amp;中文' in xml
        assert '[Content_Types].xml' in archive.namelist()


def test_generation_source_never_interpolates_content_as_executable_code(tmp_path):
    content = "'); raise RuntimeError('injected') #"
    source = generation_source('txt', content, '结果.txt', str(tmp_path))
    exec(compile(source, '<generation>', 'exec'), {'__name__': '__file_task__'})
    assert (tmp_path / '结果.txt').read_text(encoding='utf-8') == content
    # Identical retry reuses bytes; changed existing content must never be overwritten.
    exec(compile(source, '<generation>', 'exec'), {'__name__': '__file_task__'})
    (tmp_path / '结果.txt').write_text('user changed', encoding='utf-8')
    with pytest.raises(FileExistsError):
        exec(compile(source, '<generation>', 'exec'), {'__name__': '__file_task__'})


def test_source_code_files_are_saved_as_data_without_executing(tmp_path):
    source = generation_source('py', "raise RuntimeError('must not execute')", 'example.py', str(tmp_path))
    exec(compile(source, '<generation>', 'exec'), {'__name__': '__file_task__'})
    assert (tmp_path / 'example.py').read_text(encoding='utf-8') == "raise RuntimeError('must not execute')"


@pytest.mark.parametrize('name', ['../bad.txt', '/root.txt', 'a/b.txt', 'a\\b.txt'])
def test_generation_rejects_paths(name):
    with pytest.raises(ValueError):
        generation_source('txt', 'data', name)


def test_generated_docx_can_be_opened_by_word_document_reader():
    docx = pytest.importorskip('docx', reason='format QA reader')
    from io import BytesIO
    document = docx.Document(BytesIO(render_file('docx', '中文报告\n结果为42')))
    assert [paragraph.text for paragraph in document.paragraphs] == ['中文报告', '结果为42']


def test_generated_spreadsheet_is_deterministic_and_cells_are_not_formulas():
    openpyxl = pytest.importorskip('openpyxl', reason='shipped in sandbox image')
    from io import BytesIO
    first = render_file('xlsx', '姓名,内容\n小明,=1+1')
    assert first == render_file('xlsx', '姓名,内容\n小明,=1+1')
    workbook = openpyxl.load_workbook(BytesIO(first))
    assert workbook.active['B2'].value == '=1+1'
    assert workbook.active['B2'].data_type == 's'


def test_generated_pdf_and_chart_are_readable_files():
    pytest.importorskip('matplotlib', reason='shipped in sandbox image')
    from io import BytesIO
    from pypdf import PdfReader
    from PIL import Image
    pdf = render_file('pdf', 'A report\nResult: 42')
    assert 'Result: 42' in PdfReader(BytesIO(pdf)).pages[0].extract_text()
    chart = render_file('png', json.dumps(dict(labels=['a', 'b'], values=[1, 2], title='Chart')))
    with Image.open(BytesIO(chart)) as image:
        assert image.width >= 500 and image.format == 'PNG'
    jpeg = render_file('jpeg', json.dumps(dict(labels=['a'], values=[1])))
    with Image.open(BytesIO(jpeg)) as image:
        assert image.format == 'JPEG'
