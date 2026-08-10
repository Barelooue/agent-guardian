"""Screen capture + JPEG compression for Phase 2 snapshots."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import NamedTuple

from agent_guardian.schemas import SnapshotRef

logger = logging.getLogger(__name__)

# Protocol recommendation: ≤ 512 KiB for base64 payloads
MAX_SNAPSHOT_BYTES = 512 * 1024
DEFAULT_MAX_EDGE = 1600


class CaptureError(Exception):
    """Screenshot failed (permissions, backend missing, etc.)."""


class Region(NamedTuple):
    left: int
    top: int
    width: int
    height: int


def capture_snapshot(
    *,
    region: Region | tuple[int, int, int, int] | None = None,
    max_bytes: int = MAX_SNAPSHOT_BYTES,
    max_edge: int = DEFAULT_MAX_EDGE,
) -> SnapshotRef:
    """
    Capture screen (full or region), compress to JPEG under max_bytes.

    Raises CaptureError on hard failures. Callers may catch and degrade to text-only.
    """
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise CaptureError("Pillow 未安装，无法截图。请执行: pip install pillow") from exc

    bbox = None
    if region is not None:
        if isinstance(region, Region):
            left, top, width, height = region
        else:
            left, top, width, height = region
        bbox = (left, top, left + width, top + height)

    try:
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
    except Exception as exc:
        raise CaptureError(f"截图失败（可能无屏幕权限）: {exc}") from exc

    if image.mode != "RGB":
        image = image.convert("RGB")

    # Downscale long edge
    w, h = image.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))

    data, quality = _encode_jpeg_under_limit(image, max_bytes=max_bytes)
    width, height = image.size
    digest = hashlib.sha256(data).hexdigest()
    b64 = base64.b64encode(data).decode("ascii")

    logger.debug(
        "snapshot captured %dx%d quality=%s size=%s",
        width,
        height,
        quality,
        len(data),
    )
    return SnapshotRef(
        content_type="image/jpeg",
        width=width,
        height=height,
        size_bytes=len(data),
        sha256=digest,
        base64=b64,
        url=None,
    )


def _encode_jpeg_under_limit(image, *, max_bytes: int) -> tuple[bytes, int]:
    from PIL import Image

    assert isinstance(image, Image.Image)
    quality = 85
    raw = b""
    for q in range(85, 24, -10):
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=q, optimize=True)
        raw = buf.getvalue()
        quality = q
        if len(raw) <= max_bytes:
            return raw, quality

    # Last resort: shrink further
    w, h = image.size
    for _ in range(4):
        w, h = max(1, w // 2), max(1, h // 2)
        small = image.resize((w, h))
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=40, optimize=True)
        raw = buf.getvalue()
        if len(raw) <= max_bytes:
            image = small
            return raw, 40

    raise CaptureError(f"截图压缩后仍超过上限 {max_bytes} bytes（最终 {len(raw)} bytes）")


def try_capture_snapshot(
    *,
    region: Region | tuple[int, int, int, int] | None = None,
    max_bytes: int = MAX_SNAPSHOT_BYTES,
    max_edge: int = DEFAULT_MAX_EDGE,
) -> SnapshotRef | None:
    """
    Best-effort capture: never raises.

    Returns None when Pillow is missing, display/permission fails, or encode fails.
    Callers should degrade to text-only intervention cards.
    """
    try:
        return capture_snapshot(region=region, max_bytes=max_bytes, max_edge=max_edge)
    except CaptureError as exc:
        logger.warning("screenshot capture degraded to text-only: %s", exc)
        return None
    except Exception as exc:
        logger.warning("unexpected screenshot failure, degrade to text-only: %s", exc)
        return None


def decode_snapshot_bytes(snapshot: SnapshotRef) -> bytes | None:
    if not snapshot.base64:
        return None
    try:
        return base64.b64decode(snapshot.base64)
    except Exception:
        return None
