"""Phase 2 snapshot compression / media store tests."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from PIL import Image

from agent_guardian.daemon.media import MediaStore
from agent_guardian.schemas import SnapshotRef
from agent_guardian.snapshot import CaptureError, _encode_jpeg_under_limit, capture_snapshot


def test_encode_jpeg_under_limit() -> None:
    img = Image.new("RGB", (2000, 1200), color=(30, 120, 200))
    data, quality = _encode_jpeg_under_limit(img, max_bytes=80_000)
    assert len(data) <= 80_000
    assert quality <= 85


def test_media_store_materialize_and_strip_base64(tmp_path: Path) -> None:
    store = MediaStore(tmp_path / "media")
    raw = Image.new("RGB", (40, 30), color=(255, 0, 0))
    import io

    buf = io.BytesIO()
    raw.save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    snap = SnapshotRef(
        content_type="image/jpeg",
        width=40,
        height=30,
        size_bytes=len(data),
        sha256="a" * 64,
        base64=base64.b64encode(data).decode("ascii"),
    )
    out = store.materialize("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", snap)
    assert out is not None
    assert out.base64 is None
    assert out.url == "/v1/media/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.jpg"
    path = store.resolve_path("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.jpg")
    assert path is not None
    assert path.read_bytes() == data


def test_media_store_drops_oversized(tmp_path: Path) -> None:
    store = MediaStore(tmp_path / "media")
    huge = b"x" * (512 * 1024 + 10)
    snap = SnapshotRef(
        content_type="image/jpeg",
        size_bytes=len(huge),
        sha256="b" * 64,
        base64=base64.b64encode(huge).decode("ascii"),
    )
    assert store.materialize("cccccccc-cccc-4ccc-8ccc-cccccccccccc", snap) is None


def test_capture_snapshot_smoke() -> None:
    """May fail in headless CI without display — skip then."""
    try:
        snap = capture_snapshot()
    except CaptureError as exc:
        pytest.skip(f"no screen capture in this environment: {exc}")
    assert snap.content_type == "image/jpeg"
    assert snap.base64
    assert snap.size_bytes and snap.size_bytes <= 512 * 1024
