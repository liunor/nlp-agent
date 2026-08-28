"""Opt-in persistent project storage with traversal and symlink defenses."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from contextlib import contextmanager
from uuid import uuid4

from configs.settings import settings


class ProjectStorage(Protocol):
    def put(self, project_id: str, relative_path: str, content: bytes) -> None: ...
    def get(self, project_id: str, relative_path: str) -> bytes: ...


class DisabledProjectStorage:
    def put(self, project_id: str, relative_path: str, content: bytes) -> None:
        del project_id, relative_path, content
        raise PermissionError("persistent Sandbox Project Storage is not enabled")

    def get(self, project_id: str, relative_path: str) -> bytes:
        del project_id, relative_path
        raise PermissionError("persistent Sandbox Project Storage is not enabled")


class LocalProjectStorage:
    def __init__(self, root: Path, *, enabled: bool = False) -> None:
        self.root = root.resolve()
        self.enabled = enabled
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str, relative_path: str) -> Path:
        if not self.enabled:
            raise PermissionError("persistent Sandbox Project Storage is not enabled")
        project_parts = Path(project_id).parts
        if (
            not project_id
            or len(project_parts) != 1
            or project_parts[0] in {"", ".", ".."}
            or not relative_path
            or Path(relative_path).is_absolute()
        ):
            raise ValueError("project path must be relative and non-empty")
        raw_path = self.root / project_id / relative_path
        current = self.root
        try:
            relative_parts = raw_path.relative_to(self.root).parts
        except ValueError as error:
            raise ValueError("project path escapes the storage root") from error
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("symlinked project paths are not allowed")
        project = (self.root / project_id).resolve()
        path = raw_path.resolve()
        if self.root not in path.parents or project not in path.parents:
            raise ValueError("project path escapes the storage root")
        if project.is_symlink() or path.is_symlink():
            raise ValueError("symlinked project paths are not allowed")
        return path

    @staticmethod
    def _supports_secure_dirfd() -> bool:
        directory = getattr(os, "O_DIRECTORY", 0)
        supported = getattr(os, "supports_dir_fd", set())
        return bool(
            directory
            and os.open in supported
            and os.mkdir in supported
            and (os.replace in supported or os.rename in supported)
        )

    @contextmanager
    def _secure_parent(
        self, project_id: str, relative_path: str, *, create: bool
    ):
        """Open every parent component with O_NOFOLLOW and retain its fd.

        A pathname validated by ``_path`` can still be redirected by replacing
        a parent directory before ``mkdir``/``replace``.  Holding directory
        descriptors makes the final operation relative to the checked inode,
        closing that TOCTOU window on the Linux deployment platform.
        """
        if not self._supports_secure_dirfd():
            yield None, Path(relative_path).name
            return
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        root_fd = os.open(str(self.root), os.O_RDONLY | directory | nofollow)
        current_fd = root_fd
        try:
            parts = [project_id, *Path(relative_path).parts]
            for component in parts[:-1]:
                if component in {"", ".", ".."}:
                    raise ValueError("project path must be normalized")
                if create:
                    try:
                        os.mkdir(component, 0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(
                    component, os.O_RDONLY | directory | nofollow, dir_fd=current_fd
                )
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            yield current_fd, parts[-1]
        finally:
            if current_fd != root_fd:
                os.close(current_fd)
            os.close(root_fd)

    def put(self, project_id: str, relative_path: str, content: bytes) -> None:
        path = self._path(project_id, relative_path)
        if self._supports_secure_dirfd():
            with self._secure_parent(project_id, relative_path, create=True) as (parent_fd, leaf):
                assert parent_fd is not None
                temporary = f".{leaf}.{os.getpid()}.{uuid4().hex}.tmp"
                descriptor = -1
                try:
                    descriptor = os.open(
                        temporary,
                        os.O_CREAT
                        | os.O_EXCL
                        | os.O_WRONLY
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    with os.fdopen(descriptor, "wb") as stream:
                        descriptor = -1
                        stream.write(content)
                    os.replace(
                        temporary,
                        leaf,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                    try:
                        os.unlink(temporary, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.is_symlink():
            raise ValueError("symlinked project paths are not allowed")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def get(self, project_id: str, relative_path: str) -> bytes:
        path = self._path(project_id, relative_path)
        if self._supports_secure_dirfd():
            with self._secure_parent(project_id, relative_path, create=False) as (parent_fd, leaf):
                assert parent_fd is not None
                try:
                    descriptor = os.open(
                        leaf,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                except (FileNotFoundError, IsADirectoryError) as error:
                    raise FileNotFoundError(relative_path) from error
                except OSError as error:
                    raise ValueError("symlinked project paths are not allowed") from error
                with os.fdopen(descriptor, "rb") as stream:
                    return stream.read()
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(relative_path)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError as error:
            raise FileNotFoundError(relative_path) from error
        except OSError as error:
            raise ValueError("symlinked project paths are not allowed") from error
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()


def create_project_storage(*, enabled: bool, root: Path | None) -> ProjectStorage:
    """Return the explicit opt-in backend; persistence is never implicit."""
    if not enabled or root is None:
        return DisabledProjectStorage()
    return LocalProjectStorage(root, enabled=True)


def configured_project_storage() -> ProjectStorage:
    configured_root = settings.NLP_AGENT_SANDBOX_PROJECT_STORAGE_ROOT.strip()
    return create_project_storage(
        enabled=settings.NLP_AGENT_SANDBOX_PROJECT_STORAGE_ENABLED,
        root=Path(configured_root) if configured_root else None,
    )
