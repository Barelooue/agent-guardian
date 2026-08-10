"""Persist snapshot blobs and expose local preview URLs."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

from agent_guardian.schemas import SnapshotRef
from agent_guardian.snapshot import MAX_SNAPSHOT_BYTES

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[\w.\-]+$")


class MediaStore:
    def __init__(self, root: Path, *, public_prefix: str = "/v1/media") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_prefix = public_prefix.rstrip("/")

    def materialize(
        self,
        intervention_id: str,
        snapshot: SnapshotRef | None,
        *,
        max_bytes: int = MAX_SNAPSHOT_BYTES,
    ) -> SnapshotRef | None:
        """
        Save base64 snapshot to disk and return SnapshotRef with url set.
        Oversized / invalid images are dropped (caller degrades to text).
        """
        if snapshot is None:
            return None

        data: bytes | None = None
        if snapshot.base64:
            try:
                data = base64.b64decode(snapshot.base64)
            except Exception as exc:
                logger.warning("invalid snapshot base64: %s", exc)
                return None

        if data is None and snapshot.url:
            # Already a URL (possibly external) — keep as-is without base64
            return snapshot.model_copy(update={"base64": None})

        if data is None:
            return None

        if len(data) > max_bytes:
            logger.warning(
                "snapshot too large (%s > %s), stripping image",
                len(data),
                max_bytes,
            )
            return None

        ext = _ext_for_content_type(snapshot.content_type)
        filename = f"{intervention_id}{ext}"
        path = self.root / filename
        path.write_bytes(data)

        return SnapshotRef(
            content_type=snapshot.content_type or "image/jpeg",
            width=snapshot.width,
            height=snapshot.height,
            size_bytes=len(data),
            sha256=snapshot.sha256,
            url=f"{self.public_prefix}/{filename}",
            base64=None,  # avoid bloating SQLite / WS payloads
        )

    def resolve_path(self, filename: str) -> Path | None:
        if not _SAFE_NAME.match(filename):
            return None
        path = (self.root / filename).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            return None
        if not path.is_file():
            return None
        return path


def _ext_for_content_type(content_type: str | None) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "text/plain": ".txt",
    }
    return mapping.get(content_type or "", ".jpg")
