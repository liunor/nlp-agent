"""Controlled-path and real Pillow decoding tests for image inputs."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from server.tools.vision.contracts import VisionError, VisionErrorCode
from server.tools.vision.input_resolver import ImageInputResolver
from server.tools.vision.input_resolver import (
    delete_session_uploads,
    session_uploads_root,
)
from core.session_context import SessionContext
from server.tools.vision.imaging import decode_bgr
from server.tools.vision.safety import ImageSafetyLimits


def _write_image(
    path: Path,
    *,
    size: tuple[int, int] = (64, 64),
    image_format: str = "PNG",
) -> Path:
    Image.new("RGB", size, color=(10, 20, 30)).save(path, format=image_format)
    return path


def _resolver(
    tmp_path: Path,
    *,
    max_file_bytes: int = 10_000_000,
    max_pixels: int = 40_000_000,
) -> tuple[ImageInputResolver, Path]:
    uploads = tmp_path / ".data" / "uploads"
    uploads.mkdir(parents=True)
    resolver = ImageInputResolver(
        uploads_root=uploads,
        project_root=tmp_path,
        limits=ImageSafetyLimits(
            max_file_bytes=max_file_bytes,
            max_pixels=max_pixels,
        ),
    )
    return resolver, uploads


def _assert_code(resolver: ImageInputResolver, reference: str, code: VisionErrorCode):
    with pytest.raises(VisionError) as excinfo:
        resolver.resolve(reference)
    assert excinfo.value.code is code


@pytest.mark.parametrize(
    ("image_format", "suffix", "media_type"),
    [
        ("JPEG", ".jpg", "image/jpeg"),
        ("PNG", ".png", "image/png"),
        ("WEBP", ".webp", "image/webp"),
    ],
)
def test_resolver_accepts_only_supported_decoded_formats(
    tmp_path: Path, image_format: str, suffix: str, media_type: str
) -> None:
    resolver, uploads = _resolver(tmp_path)
    path = _write_image(uploads / f"sample{suffix}", image_format=image_format)

    asset = resolver.resolve(str(path))

    assert asset.reference.file_name == path.name
    assert asset.reference.media_type == media_type
    assert asset.reference.width == 64
    assert asset.reference.height == 64
    assert asset.reference.frames == 1
    assert len(asset.reference.sha256) == 64
    assert str(path.parent) not in asset.reference.model_dump_json()


def test_resolver_accepts_project_relative_upload_path(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path)
    _write_image(uploads / "relative.png")

    asset = resolver.resolve(".data/uploads/relative.png")

    assert asset.reference.file_name == "relative.png"


def test_resolver_accepts_bare_filename_inside_session_upload_root(
    tmp_path: Path,
) -> None:
    resolver, uploads = _resolver(tmp_path)
    _write_image(uploads / "session-image.png")

    asset = resolver.resolve("session-image.png")

    assert asset.reference.file_name == "session-image.png"


def test_exif_rotation_uses_same_dimensions_as_decoded_coordinate_space(
    tmp_path: Path,
) -> None:
    resolver, uploads = _resolver(tmp_path)
    path = uploads / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (80, 120), color=(10, 20, 30)).save(
        path, format="JPEG", exif=exif
    )

    asset = resolver.resolve("rotated.jpg")
    decoded = decode_bgr(asset.data, auto_rotate=True)

    assert (asset.reference.width, asset.reference.height) == (120, 80)
    assert (decoded.shape[1], decoded.shape[0]) == (120, 80)


@pytest.mark.parametrize(
    "url", ["https://example.com/image.png", "http://example.com/image.jpg"]
)
def test_resolver_rejects_remote_urls(tmp_path: Path, url: str) -> None:
    resolver, _ = _resolver(tmp_path)
    _assert_code(resolver, url, VisionErrorCode.REMOTE_URL_DISABLED)


def test_resolver_rejects_path_outside_uploads(tmp_path: Path) -> None:
    resolver, _ = _resolver(tmp_path)
    outside = _write_image(tmp_path / "outside.png")
    _assert_code(resolver, str(outside), VisionErrorCode.PATH_NOT_ALLOWED)


def test_resolver_rejects_lexical_path_traversal(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path)
    _write_image(tmp_path / ".data" / "outside.png")
    reference = str(uploads / ".." / "outside.png")
    _assert_code(resolver, reference, VisionErrorCode.PATH_NOT_ALLOWED)


def test_resolver_rejects_symlink_even_when_target_is_inside_uploads(
    tmp_path: Path,
) -> None:
    resolver, uploads = _resolver(tmp_path)
    target = _write_image(uploads / "target.png")
    link = uploads / "linked.png"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"platform does not permit symlink creation: {error}")

    _assert_code(resolver, str(link), VisionErrorCode.UNSAFE_PATH)


def test_resolver_rejects_file_over_byte_limit(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path, max_file_bytes=64)
    path = uploads / "large.png"
    path.write_bytes(b"x" * 65)
    _assert_code(resolver, str(path), VisionErrorCode.FILE_TOO_LARGE)


def test_resolver_rejects_image_smaller_than_48_pixels(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path)
    path = _write_image(uploads / "tiny.png", size=(47, 64))
    _assert_code(resolver, str(path), VisionErrorCode.IMAGE_TOO_SMALL)


def test_resolver_rejects_image_over_pixel_limit(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path, max_pixels=2_000)
    path = _write_image(uploads / "too-many-pixels.png", size=(48, 48))
    _assert_code(resolver, str(path), VisionErrorCode.IMAGE_TOO_LARGE)


def test_resolver_rejects_multiframe_image(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path)
    path = uploads / "animated.gif"
    first = Image.new("RGB", (64, 64), color="red")
    second = Image.new("RGB", (64, 64), color="blue")
    first.save(path, format="GIF", save_all=True, append_images=[second], duration=10)
    _assert_code(resolver, str(path), VisionErrorCode.MULTI_FRAME_UNSUPPORTED)


def test_resolver_rejects_unsupported_single_frame_format(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path)
    path = _write_image(uploads / "sample.bmp", image_format="BMP")
    _assert_code(resolver, str(path), VisionErrorCode.UNSUPPORTED_MEDIA_TYPE)


def test_resolver_rejects_corrupt_image(tmp_path: Path) -> None:
    resolver, uploads = _resolver(tmp_path)
    path = uploads / "corrupt.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-complete-image")
    _assert_code(resolver, str(path), VisionErrorCode.INVALID_IMAGE)


def test_session_upload_cleanup_removes_only_owned_namespace(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    context = SessionContext(
        session_id="session-1", user_id="alice", workspace_id="course-1"
    )
    target = session_uploads_root(context, uploads_root=uploads)
    sibling = uploads / "course-1" / "alice" / "session-2"
    target.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (target / "private.png").write_bytes(b"private")
    (sibling / "keep.png").write_bytes(b"keep")

    assert delete_session_uploads(context, uploads_root=uploads) is True
    assert not target.exists()
    assert (sibling / "keep.png").read_bytes() == b"keep"
    assert delete_session_uploads(context, uploads_root=uploads) is False
