"""Run untrusted document parsing outside the Web/Worker event loop."""
from __future__ import annotations

import asyncio
import json
import subprocess
import os
from contextlib import asynccontextmanager
import sys
from pathlib import Path


class DocumentError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_slots = asyncio.Semaphore(1)


@asynccontextmanager
async def _parser_slot():
    try:
        await asyncio.wait_for(_slots.acquire(), timeout=3)
    except TimeoutError as exc:
        raise DocumentError('parser_busy', '文档读取繁忙，请稍后重试') from exc
    try:
        yield
    finally:
        _slots.release()


async def read_document(path: Path, *, name: str, expected_hash: str | None = None,
                        offset: int = 0, limit: int = 20, max_chars: int = 12_000,
                        char_offset: int = 0) -> dict:
    if offset < 0 or offset > 100_000 or not 1 <= limit <= 100 or not 1 <= max_chars <= 24_000 or not 0 <= char_offset <= 25 * 1024 * 1024:
        raise DocumentError('invalid_range', '读取范围超出限制')
    request = dict(path=str(path.resolve()), name=name, expected_hash=expected_hash,
                   offset=offset, limit=limit, max_chars=max_chars, char_offset=char_offset)
    async with _parser_slot():
        process = subprocess.Popen(
            [sys.executable, '-m', 'server.documents.parser_worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parents[2]),
            env={key: value for key, value in os.environ.items()
                 if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}},
        )
        try:
            stdout, _ = await asyncio.wait_for(
                asyncio.to_thread(process.communicate, json.dumps(request).encode()), timeout=30,
            )
        except BaseException as error:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait)
            if isinstance(error, TimeoutError):
                raise DocumentError('parse_timeout', '文档解析超时，请缩小读取范围') from error
            raise
    if process.returncode or len(stdout) > 512_000:
        raise DocumentError('parse_failed', '文件解析失败或超出资源限制')
    result = json.loads(stdout)
    if 'error' in result:
        raise DocumentError(result['code'], result['error'])
    return result
