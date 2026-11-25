"""Disposable parser process. No model, database, or network operations."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from defusedxml import ElementTree as ET

from .content import DocumentError

MAX_INPUT_BYTES = 25 * 1024 * 1024
TEXT_EXTENSIONS = frozenset('txt text md markdown mdown mkd log csv tsv json jsonl py js ts jsx tsx mjs cjs css scss html htm xml yaml yml sh bash zsh sql java c cc h cpp hpp cs go rs rb php swift kt vue svelte toml ini env dockerfile makefile readme'.split())
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _docx(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(x.file_size for x in entries) > 32 * 1024 * 1024:
            raise DocumentError('resource_limit', '文档解压后超过读取限制')
        xml = archive.read('word/document.xml')
    # No DTDs/entities. Embedded media and external relationships are never fetched.
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise DocumentError('unsupported_document', '不支持包含外部实体的文档')
    root = ET.fromstring(xml)
    body = root.find(W + 'body')
    if body is None:
        raise DocumentError('parse_failed', '文档缺少正文')
    for child in body:
        if child.tag == W + 'p':
            yield 'paragraph', ''.join(t.text or '' for t in child.iter(W + 't'))
        elif child.tag == W + 'tbl':
            rows = []
            for row in child.findall(W + 'tr'):
                rows.append('\t'.join(''.join(t.text or '' for t in cell.iter(W + 't')) for cell in row.findall(W + 'tc')))
            yield 'table', '\n'.join(rows)


def _xlsx(data: bytes):
    """Read spreadsheet values/formula text only; never evaluate formulas."""
    S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > 2000 or sum(x.file_size for x in archive.infolist()) > 32 * 1024 * 1024:
            raise DocumentError('resource_limit', '表格解压后超过读取限制')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in node.iter(S + 't'))
                       for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {node.attrib['Id']: node.attrib['Target'] for node in relations
                   if node.attrib.get('TargetMode') != 'External'}
        for sheet in workbook.find(S + 'sheets'):
            relation_id = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = targets.get(relation_id, '').lstrip('/')
            if not target.startswith('xl/'):
                target = 'xl/' + target
            if '..' in target.split('/') or not target.startswith('xl/worksheets/'):
                raise DocumentError('unsupported_document', '不支持该工作表引用')
            root = ET.fromstring(archive.read(target))
            for row in root.iter(S + 'row'):
                cells = []
                for cell in row.findall(S + 'c'):
                    value = cell.find(S + 'v')
                    text = value.text or '' if value is not None else ''
                    if cell.attrib.get('t') == 's':
                        text = strings[int(text)] if text else ''
                    elif cell.attrib.get('t') == 'inlineStr':
                        text = ''.join(t.text or '' for t in cell.iter(S + 't'))
                    formula = cell.find(S + 'f')
                    if formula is not None:
                        text = '[公式未计算] ' + (formula.text or '') + (' [缓存值] ' + text if text else '')
                    cells.append(cell.attrib.get('r', '') + ': ' + text)
                yield 'row', f"[{sheet.attrib.get('name', '')}] 行 {row.attrib.get('r', '')}\n" + '\t'.join(cells)


def parse(request: dict) -> dict:
    with Path(request['path']).open('rb') as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise DocumentError('resource_limit', '文件超过文档读取大小限制')
    digest = hashlib.sha256(data).hexdigest()
    if request.get('expected_hash') and request['expected_hash'] != digest:
        raise DocumentError('version_changed', '文件内容已变化，请重新选择当前版本')
    extension = Path(request['name']).suffix.lower().lstrip('.') or Path(request['name']).name.lower().lstrip('.')
    offset, limit, remaining = request['offset'], request['limit'], request['max_chars']
    warnings = []
    if extension == 'docx':
        parts = _docx(data)
        unit = 'block'
        warnings.append('阅读视图；未提取图片、图表及复杂排版对象')
    elif extension == 'xlsx':
        parts = _xlsx(data)
        unit = 'row'
        warnings.append('读取工作表单元格值；公式不执行，图表和格式未提取')
    elif extension == 'pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError('encrypted', '暂不支持密码保护的 PDF')
        if len(reader.pages) > 1000:
            raise DocumentError('resource_limit', 'PDF 页数超过读取限制')
        # Skip unrequested pages without extracting their content streams.
        parts = (('page', reader.pages[i].extract_text() or '') if offset <= i < offset + min(limit, 3)
                 else ('page', '') for i in range(len(reader.pages)))
        limit = min(limit, 3)
        unit = 'page'
        warnings.append('按页提取文本；扫描页需要 OCR，图表和复杂表格可能未完整提取')
    elif extension in TEXT_EXTENSIONS:
        if b'\x00' in data:
            raise DocumentError('unsupported_document', '不支持将二进制文件作为文本读取')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = data.decode('gb18030')
            except UnicodeDecodeError as exc:
                raise DocumentError('unsupported_encoding', '无法识别文件编码') from exc
        parts = (('line', line) for line in text.splitlines())
        unit = 'line'
    else:
        raise DocumentError('unsupported_document', '该文件格式暂不支持内容读取，请下载查看')
    blocks, next_offset, truncated = [], None, False
    next_char_offset = 0
    for index, (kind, text) in enumerate(parts):
        if index < offset:
            continue
        if len(blocks) >= limit or remaining <= 0:
            next_offset = index
            truncated = True
            break
        char_offset = request.get('char_offset', 0) if index == offset else 0
        text = text[char_offset:]
        clipped = len(text) > remaining
        blocks.append(dict(kind=kind, number=index + 1, text=text[:remaining], truncated=clipped,
                           needs_ocr=kind == 'page' and not text.strip()))
        remaining -= min(len(text), remaining)
        if clipped:
            warnings.append(f'第 {index + 1} 个内容单元超出单次字符预算，仅返回前段')
            truncated = True
            next_offset = index
            next_char_offset = char_offset + len(blocks[-1]['text'])
            break
    return dict(content='\n'.join(b['text'] for b in blocks), blocks=blocks, sha256=digest,
                unit=unit, offset=offset, char_offset=request.get("char_offset", 0), next_offset=next_offset, next_char_offset=next_char_offset,
                truncated=truncated, warnings=warnings)


def main():
    if sys.platform != 'win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    try:
        request = json.loads(sys.stdin.buffer.read(8193))
        result = parse(request)
    except DocumentError as exc:
        result = dict(code=exc.code, error=str(exc))
    except FileNotFoundError:
        result = dict(code='version_changed', error='文件在排队期间已变化，请重新读取')
    except Exception:
        result = dict(code='parse_failed', error='文件损坏、格式不匹配或超出解析限制')
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
