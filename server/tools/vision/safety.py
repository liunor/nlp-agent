"""Local-path and decoded-image safety checks for phase-one vision support."""

from __future__ import annotations

import hashlib
import os
import stat
import warnings
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from server.tools.vision.contracts import (
    ImageAsset,
    ImageReference,
    VisionError,
    VisionErrorCode,
)


SUPPORTED_FORMATS: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
SUPPORTED_MEDIA_TYPES = frozenset(SUPPORTED_FORMATS.values())
_EXIF_ORIENTATION_TAG = 274
_EXIF_DIMENSION_SWAPS = frozenset({5, 6, 7, 8})


@dataclass(frozen=True, slots=True)
class ImageSafetyLimits:
    max_file_bytes: int = 10_000_000
    max_pixels: int = 40_000_000
    min_dimension: int = 48
    allowed_media_types: frozenset[str] = field(
        default_factory=lambda: SUPPORTED_MEDIA_TYPES
    )

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        if self.max_pixels <= 0:
            raise ValueError("max_pixels must be positive")
        if self.min_dimension <= 0:
            raise ValueError("min_dimension must be positive")


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except (FileNotFoundError, OSError):
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _contains_link_or_reparse_point(root: Path, candidate: Path) -> bool:
    if _is_link_or_reparse_point(root):
        return True
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link_or_reparse_point(current):
            return True
    return False


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((str(candidate), str(root)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(root))


def resolve_controlled_path(
    raw_reference: str,
    *,
    uploads_root: Path,
    project_root: Path,
) -> Path:
    """Resolve a path lexically and physically inside ``.data/uploads``."""

    reference = raw_reference.strip()
    lowered = reference.lower()
    if lowered.startswith(("http://", "https://")):
        raise VisionError(
            VisionErrorCode.REMOTE_URL_DISABLED,
            "第一期不允许读取远程图片 URL",
        )
    if "://" in reference or "\x00" in reference:
        raise VisionError(
            VisionErrorCode.INVALID_IMAGE_REFERENCE,
            "图片引用不是有效的本地上传路径",
        )

    supplied = Path(reference)
    if not reference or ".." in supplied.parts:
        raise VisionError(
            VisionErrorCode.PATH_NOT_ALLOWED,
            "图片路径不在允许的 .data/uploads 目录中",
        )

    # Bare filename (no directory separator) resolves inside uploads_root.
    if "/" not in reference and "\\" not in reference:
        candidate = uploads_root / reference
    else:
        candidate = supplied if supplied.is_absolute() else project_root / supplied
    lexical_root = Path(os.path.abspath(uploads_root))
    lexical_candidate = Path(os.path.abspath(candidate))
    if not _is_within(lexical_candidate, lexical_root):
        raise VisionError(
            VisionErrorCode.PATH_NOT_ALLOWED,
            "图片路径不在允许的 .data/uploads 目录中",
        )

    if _contains_link_or_reparse_point(lexical_root, lexical_candidate):
        raise VisionError(
            VisionErrorCode.UNSAFE_PATH,
            "图片路径包含符号链接或重解析点",
        )

    if not lexical_candidate.exists():
        raise VisionError(VisionErrorCode.FILE_NOT_FOUND, "图片文件不存在")
    if not lexical_candidate.is_file():
        raise VisionError(VisionErrorCode.NOT_A_FILE, "图片引用不是普通文件")

    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved_candidate = lexical_candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise VisionError(
            VisionErrorCode.UNSAFE_PATH,
            "无法安全解析图片路径",
        ) from error
    if not _is_within(resolved_candidate, resolved_root):
        raise VisionError(
            VisionErrorCode.PATH_NOT_ALLOWED,
            "图片路径不在允许的 .data/uploads 目录中",
        )
    return resolved_candidate


def _read_limited(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise VisionError(
            VisionErrorCode.INVALID_IMAGE,
            "无法读取图片元数据",
        ) from error
    if size > limit:
        raise VisionError(
            VisionErrorCode.FILE_TOO_LARGE,
            f"图片超过 {limit} 字节上限",
        )
    try:
        with path.open("rb") as file:
            data = file.read(limit + 1)
    except OSError as error:
        raise VisionError(VisionErrorCode.INVALID_IMAGE, "无法读取图片") from error
    if len(data) > limit:
        raise VisionError(
            VisionErrorCode.FILE_TOO_LARGE,
            f"图片超过 {limit} 字节上限",
        )
    return data


def _inspect_image(data: bytes, limits: ImageSafetyLimits) -> tuple[str, int, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = (image.format or "").upper()
                width, height = image.size
                frames = int(getattr(image, "n_frames", 1))
                if frames != 1:
                    raise VisionError(
                        VisionErrorCode.MULTI_FRAME_UNSUPPORTED,
                        "第一期不支持多帧图片",
                    )
                media_type = SUPPORTED_FORMATS.get(image_format)
                if (
                    media_type is None
                    or media_type not in limits.allowed_media_types
                ):
                    raise VisionError(
                        VisionErrorCode.UNSUPPORTED_MEDIA_TYPE,
                        "仅支持 JPEG、PNG 和 WebP 图片",
                    )
                if width < limits.min_dimension or height < limits.min_dimension:
                    raise VisionError(
                        VisionErrorCode.IMAGE_TOO_SMALL,
                        f"图片宽高均须至少为 {limits.min_dimension} 像素",
                    )
                if width * height > limits.max_pixels:
                    raise VisionError(
                        VisionErrorCode.IMAGE_TOO_LARGE,
                        f"图片超过 {limits.max_pixels} 像素上限",
                    )
                image.verify()

            with Image.open(BytesIO(data)) as decoded:
                decoded.seek(0)
                orientation = int(decoded.getexif().get(_EXIF_ORIENTATION_TAG, 1))
                decoded.load()
                if orientation in _EXIF_DIMENSION_SWAPS:
                    width, height = height, width
    except VisionError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise VisionError(
            VisionErrorCode.IMAGE_TOO_LARGE,
            "图片像素规模超过安全上限",
        ) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise VisionError(
            VisionErrorCode.INVALID_IMAGE,
            "图片损坏或无法解码",
        ) from error

    return media_type, width, height, frames


def load_validated_image(path: Path, limits: ImageSafetyLimits) -> ImageAsset:
    data = _read_limited(path, limits.max_file_bytes)
    media_type, width, height, frames = _inspect_image(data, limits)
    reference = ImageReference(
        file_name=path.name,
        media_type=media_type,
        size_bytes=len(data),
        width=width,
        height=height,
        frames=frames,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return ImageAsset(path=path, data=data, reference=reference)
