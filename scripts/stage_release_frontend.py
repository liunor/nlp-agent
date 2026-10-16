"""Stage immutable frontend assets; never edit runtime YAML or remove old files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def guard(path: Path) -> None:
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_STATIC_PATH")


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(directory: Path) -> list[Path]:
    guard(directory)
    require(directory.is_dir(), "STATIC_DIRECTORY_MISSING")
    result = []
    total = 0
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "SYMLINK_STATIC_ASSET")
        if path.is_file():
            total += path.stat().st_size
            require(path.stat().st_size < 64 * 1024 * 1024 and total < 512 * 1024 * 1024,
                    "STATIC_ASSETS_TOO_LARGE_REVIEW_REQUIRED")
            result.append(path)
            require(len(result) <= 10000, "TOO_MANY_STATIC_ASSETS_REVIEW_REQUIRED")
    require((directory / "index.html").is_file(), "STATIC_INDEX_MISSING")
    return result


def entry_assets(directory: Path) -> None:
    for ref in re.findall(r'(?:src|href)=["\']([^"\']+)["\']',
                          (directory / "index.html").read_text(encoding="utf-8")):
        url = urlsplit(ref)
        if url.scheme or url.netloc or not url.path:
            continue
        name = unquote(url.path).lstrip("/")
        path = PurePosixPath(name)
        require(".." not in path.parts and "\\" not in name and not path.is_absolute(),
                "UNSAFE_ENTRY_ASSET_REFERENCE")
        require((directory / name).is_file(), "ENTRY_ASSET_MISSING")


def stage_frontend(source: Path, previous: Path, root: Path) -> dict:
    source_files, previous_files = files(source), files(previous)
    entry_assets(source)
    guard(root)
    index_sha = checksum(source / "index.html")
    expected = {path.relative_to(source).as_posix(): checksum(path) for path in source_files}
    target = root / index_sha / "dist"
    guard(target)
    if target.exists():
        files(target)
        require(all((target / name).is_file() and not (target / name).is_symlink()
                    and checksum(target / name) == value for name, value in expected.items()),
                "EXISTING_FRONTEND_CHANGED")
        entry_assets(target)
        return {"status": "RELEASE_FRONTEND_ALREADY_STAGED", "static_dir": str(target),
                "index_sha256": index_sha}
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=root))
    destination = temporary / "dist"
    destination.mkdir()
    # Keep hashed chunks required by already-open tabs. The new build wins for
    # shared names such as index.html. No previous release directory is deleted.
    for base, values in ((previous, previous_files), (source, source_files)):
        for path in values:
            output = destination / path.relative_to(base)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
    require(all(checksum(destination / name) == value for name, value in expected.items()),
            "STAGED_FRONTEND_CHECKSUM_MISMATCH")
    entry_assets(destination)
    for path in temporary.rglob("*"):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    os.chmod(temporary, 0o755)
    os.rename(temporary, target.parent)
    return {"status": "RELEASE_FRONTEND_STAGED", "static_dir": str(target),
            "index_sha256": index_sha, "runtime_configuration_changed": False,
            "previous_assets_retained": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", required=True)
    args = parser.parse_args()
    previous = Path(args.previous)
    require(previous.is_absolute() and
            (previous.is_relative_to(Path("/app/.data/developer"))
             or previous == Path("/previous-frontend")), "UNEXPECTED_PREVIOUS_STATIC_PATH")
    print(json.dumps(stage_frontend(Path("/app/webui/dist"), previous,
                                   Path("/app/.data/developer/ci-release-frontends"))))


if __name__ == "__main__":
    main()
