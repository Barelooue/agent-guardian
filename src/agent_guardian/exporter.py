"""Export intervention history to multimodal DPO-style JSONL."""

from __future__ import annotations

import argparse
import base64
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TextIO

from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.store import InterventionRecord, InterventionStore
from agent_guardian.schemas import InterventionStatus

logger = logging.getLogger(__name__)


def _default_media_root(db_path: Path) -> Path:
    return db_path.resolve().parent / "agent_guardian_media"


def _resolve_image_path(
    record: InterventionRecord,
    *,
    media_root: Path,
) -> Path | None:
    snap = record.request.snapshot
    if snap is None:
        # Convention: Daemon MediaStore writes {intervention_id}.jpg
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = media_root / f"{record.intervention_id}{ext}"
            if candidate.is_file():
                return candidate
        return None

    if snap.url:
        # url like /v1/media/{filename}
        name = snap.url.rstrip("/").split("/")[-1]
        candidate = media_root / name
        if candidate.is_file():
            return candidate

    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = media_root / f"{record.intervention_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _build_prompt(record: InterventionRecord) -> str:
    req = record.request
    ctx = req.context or {}
    parts = [
        f"Title: {req.title}",
        f"Reason: {req.reason}",
    ]
    if ctx:
        parts.append("Context: " + json.dumps(ctx, ensure_ascii=False, sort_keys=True))
    option_lines = [f"- {o.id}: {o.label}" for o in req.options]
    if option_lines:
        parts.append("Options:\n" + "\n".join(option_lines))
    return "\n".join(parts)


def record_to_dpo(
    record: InterventionRecord,
    *,
    media_root: Path,
    embed_images: bool = False,
) -> dict[str, Any] | None:
    """
    Convert a RESOLVED intervention into one DPO-style preference row.

    Returns None when there is no human decision (should not happen for RESOLVED).
    """
    if record.status != InterventionStatus.RESOLVED:
        return None
    if record.decision is None:
        return None

    chosen = record.decision.option_id
    rejected = [o.id for o in record.request.options if o.id != chosen]
    image_path = _resolve_image_path(record, media_root=media_root)

    row: dict[str, Any] = {
        "id": record.intervention_id,
        "image_path": str(image_path) if image_path else None,
        "prompt": _build_prompt(record),
        "chosen": chosen,
        "rejected": rejected,
        "meta": {
            "client_request_id": record.client_request_id,
            "agent_id": record.request.agent_id,
            "source": record.decision.source.value if record.decision.source else None,
            "operator_id": record.decision.operator_id,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "active_channel": (record.active_channel.value if record.active_channel else None),
        },
    }

    if embed_images:
        b64: str | None = None
        if image_path and image_path.is_file():
            b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        elif record.request.snapshot and record.request.snapshot.base64:
            b64 = record.request.snapshot.base64
        row["image_base64"] = b64

    return row


async def iter_resolved_records(db_path: Path | str) -> AsyncIterator[InterventionRecord]:
    conn = await init_db(str(db_path))
    store = InterventionStore(conn)
    try:
        for record in await store.list_by_status(InterventionStatus.RESOLVED):
            yield record
    finally:
        await conn.close()


async def export_dpo_jsonl(
    db_path: Path | str,
    output: Path | str | TextIO,
    *,
    media_root: Path | str | None = None,
    embed_images: bool = False,
) -> int:
    """
    Export RESOLVED interventions to multimodal DPO JSONL.

    Returns number of rows written.
    """
    db = Path(db_path)
    media = Path(media_root) if media_root is not None else _default_media_root(db)

    close_fp = False
    fp: TextIO
    if isinstance(output, (str, Path)):
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fp = out_path.open("w", encoding="utf-8")
        close_fp = True
    else:
        fp = output

    count = 0
    try:
        async for record in iter_resolved_records(db):
            row = record_to_dpo(record, media_root=media, embed_images=embed_images)
            if row is None:
                continue
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    finally:
        if close_fp:
            fp.close()

    logger.info("exported %s DPO rows from %s", count, db)
    return count


def build_export_argparser(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Output JSONL path (e.g. dataset.jsonl)",
    )
    sub.add_argument(
        "--db",
        type=Path,
        default=Path("agent_guardian.db"),
        help="SQLite database path (default: ./agent_guardian.db)",
    )
    sub.add_argument(
        "--media-dir",
        type=Path,
        default=None,
        help="Snapshot media directory (default: <db-dir>/agent_guardian_media)",
    )
    sub.add_argument(
        "--embed-images",
        action="store_true",
        help="Also embed image_base64 in each row (larger files)",
    )


async def run_export_cli(args: argparse.Namespace) -> int:
    count = await export_dpo_jsonl(
        args.db,
        args.output,
        media_root=args.media_dir,
        embed_images=args.embed_images,
    )
    print(f"Exported {count} preference pairs → {args.output}")
    return 0
