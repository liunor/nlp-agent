"""Trusted declarative generators, sent as fixed code to a scoped sandbox."""
from __future__ import annotations

import inspect
import json

TEXT_FORMATS = frozenset('txt md py js ts tsx jsx mjs cjs css scss html xml yaml yml sql sh bash java c h cpp hpp cs go rs rb php swift kt vue svelte toml ini env log jsonl dockerfile makefile readme'.split())
FORMATS = TEXT_FORMATS | frozenset({'json', 'csv', 'tsv', 'xlsx', 'docx', 'pdf', 'png', 'jpg', 'jpeg'})


def render_file(format: str, content: str) -> bytes:
    import csv
    import io
    import json
    import zipfile
    from xml.sax.saxutils import escape

    if format in TEXT_FORMATS:
        return content.encode('utf-8')
    if format == 'json':
        return json.dumps(json.loads(content), ensure_ascii=False, indent=2).encode('utf-8')
    if format in ('csv', 'tsv'):
        rows = list(csv.reader(io.StringIO(content), delimiter='\t' if format == 'tsv' else ','))
        output = io.StringIO(newline='')
        csv.writer(output, delimiter='\t' if format == 'tsv' else ',').writerows(rows)
        return output.getvalue().encode('utf-8-sig')
    if format == 'docx':
        parts = {
            '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
            '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
            'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(line) + '</w:t></w:r></w:p>' for line in content.splitlines()) + '<w:sectPr/></w:body></w:document>',
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, data in parts.items():
                archive.writestr(zipfile.ZipInfo(name), data.encode('utf-8'))
        return output.getvalue()
    if format == 'xlsx':
        from openpyxl import Workbook
        rows = list(csv.reader(io.StringIO(content)))
        if len(rows) > 1000 or any(len(row) > 100 for row in rows):
            raise ValueError('表格超过生成范围')
        workbook = Workbook()
        for row in rows:
            workbook.active.append(row)
        # Treat model/user cell contents as data, never spreadsheet formulas.
        for row in workbook.active:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        output = io.BytesIO()
        workbook.save(output)
        # Canonical ZIP timestamps and workbook metadata make retries stable.
        from datetime import datetime
        workbook.properties.created = workbook.properties.modified = datetime(2000, 1, 1)
        output = io.BytesIO()
        workbook.save(output)
        canonical = io.BytesIO()
        with zipfile.ZipFile(output) as original, zipfile.ZipFile(canonical, 'w') as archive:
            for name in sorted(original.namelist()):
                data = original.read(name)
                if name == 'docProps/core.xml':
                    import re
                    data = re.sub(rb'(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)', rb'\g<1>2000-01-01T00:00:00Z\2', data)
                archive.writestr(zipfile.ZipInfo(name), data)
        return canonical.getvalue()
    if format in ('pdf', 'png', 'jpg', 'jpeg'):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        output = io.BytesIO()
        if format != 'pdf':
            spec = json.loads(content)
            labels, values = spec['labels'], spec['values']
            if not 1 <= len(labels) == len(values) <= 100:
                raise ValueError('图表需要1到100组标签和数值')
            values = [float(v) for v in values]
            import math
            if not all(math.isfinite(v) for v in values):
                raise ValueError('图表数值必须有限')
            fig, axis = plt.subplots(figsize=(10, 6))
            axis.bar([str(label)[:100] for label in labels], values)
            axis.set_title(str(spec.get('title', ''))[:200])
            fig.savefig(output, format='jpeg' if format in ('jpg', 'jpeg') else 'png')
            plt.close(fig)
        else:
            import textwrap
            from datetime import datetime, timezone
            from matplotlib.backends.backend_pdf import PdfPages
            lines = [wrapped for line in content.splitlines() for wrapped in (textwrap.wrap(line, 70) or [''])]
            if len(lines) > 2000:
                raise ValueError('报告超过生成范围')
            with PdfPages(output, metadata={'CreationDate': datetime(2000, 1, 1, tzinfo=timezone.utc)}) as pdf:
                for offset in range(0, max(1, len(lines)), 45):
                    fig = plt.figure(figsize=(8.27, 11.69))
                    fig.text(.08, .94, '\n'.join(lines[offset:offset + 45]), va='top', fontsize=11)
                    pdf.savefig(fig)
                    plt.close(fig)
        return output.getvalue()
    raise ValueError('不支持的生成格式')


def generation_source(format: str, content: str, filename: str,
                      directory: str = '/files/智能体生成', max_bytes: int = 25 * 1024 * 1024,
                      staging: bool = False) -> str:
    if format not in FORMATS or len(content) > 24_000 or not content.strip():
        raise ValueError('生成格式、内容或长度不符合限制')
    if not filename or len(filename) > 180 or filename in ('.', '..') or any(c in filename for c in '/\\\x00'):
        raise ValueError('文件名不能包含路径')
    if not filename.lower().endswith('.' + format) and not (format in {'dockerfile', 'makefile', 'readme'} and filename.lower() == format):
        raise ValueError('文件扩展名与生成格式不匹配')
    data = json.dumps(dict(format=format, content=content, filename=filename,
                           directory=directory, max_bytes=max_bytes, staging=staging), ensure_ascii=False)
    return 'TEXT_FORMATS = ' + repr(tuple(sorted(TEXT_FORMATS))) + '\n' + inspect.getsource(render_file) + '\n' + '''
import hashlib, json
from pathlib import Path
request = json.loads(''' + repr(data) + ''')
payload = render_file(request['format'], request['content'])
if len(payload) > request['max_bytes']:
    raise ValueError('生成文件超过单文件配额')
directory = Path(request['directory'])
directory.mkdir(parents=True, exist_ok=True)
target = directory / request['filename']
digest = hashlib.sha256(payload).hexdigest()
if request['staging']:
    # This randomly named, empty staging record was created by the Manager.
    if not target.name.startswith('.生成中-') or target.stat().st_size != 0:
        raise FileExistsError('生成暂存位置不符合预期')
    with target.open('wb') as output:
        output.write(payload)
elif target.exists():
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise FileExistsError('同名文件已存在且内容不同，未覆盖')
else:
    # Exclusive creation never truncates a pre-existing user file.
    with target.open('xb') as output:
        output.write(payload)
print(json.dumps({'path': str(target), 'sha256': digest, 'size_bytes': len(payload)}, ensure_ascii=False))
'''
