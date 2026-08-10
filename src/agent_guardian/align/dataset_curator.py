"""Curate multimodal preference datasets (Qwen2-VL / LLaVA style DPO/ORPO)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.store import InterventionRecord, InterventionStore
from agent_guardian.daemon.takeover_store import TakeoverEventRecord, TakeoverStore
from agent_guardian.exporter import record_to_dpo
from agent_guardian.schemas import InterventionStatus
from agent_guardian.ui.spatial import SpatialPromptInjector

logger = logging.getLogger(__name__)

AlignFormat = Literal["qwen2_vl", "llava", "orpo"]


@dataclass
class TakeoverTrace:
    agent_id: str
    instruction: str
    before_action: str | None = None
    before_thought: str | None = None
    after_action: str | None = None
    screenshot: str | None = None
    signal_id: str | None = None


@dataclass
class CuratorStats:
    interventions: int = 0
    spatial: int = 0
    rollbacks: int = 0
    takeovers: int = 0
    written: int = 0
    skipped: int = 0


@dataclass
class DatasetCurator:
    """
    Align Phase 6/7 artifacts into multimodal preference JSONL.

    Each row roughly follows chat preference schemas used by Qwen2-VL / LLaVA DPO:
    ``prompt`` / ``chosen`` / ``rejected`` (+ optional ``images``).
    """

    media_root: Path
    format: AlignFormat = "qwen2_vl"
    include_images: bool = True
    takeover_traces: list[TakeoverTrace] = field(default_factory=list)

    def add_takeover(self, trace: TakeoverTrace) -> None:
        self.takeover_traces.append(trace)

    def curate_record(self, record: InterventionRecord) -> dict[str, Any] | None:
        base = record_to_dpo(record, media_root=self.media_root, embed_images=False)
        if base is None:
            return None

        decision = record.decision
        spatial = decision.spatial if decision else None
        spatial_prompt = (
            SpatialPromptInjector.to_prompt(spatial) if spatial is not None else None
        )
        human_prompt = base["prompt"]
        if spatial_prompt:
            human_prompt = human_prompt + "\n\n" + spatial_prompt

        # Rejected = agent would have continued blindly / wrong option text
        rejected = self._rejected_response(record, base.get("rejected") or [])
        chosen = self._chosen_response(record, spatial_prompt)

        images: list[str] = []
        image_path = base.get("image_path")
        if self.include_images and image_path:
            images.append(str(image_path))

        row: dict[str, Any] = {
            "id": base["id"],
            "source": "agent_guardian",
            "format": self.format,
            "images": images,
            "prompt": human_prompt,
            "chosen": chosen,
            "rejected": rejected,
            "meta": {
                **(base.get("meta") or {}),
                "spatial": (
                    SpatialPromptInjector.to_structured(spatial) if spatial else None
                ),
                "rollback_steps": decision.rollback_steps if decision else None,
                "smart_code": (record.request.metadata or {}).get("smart_code"),
                "agent_id": record.request.agent_id,
            },
        }
        if self.format == "llava":
            row["conversations"] = [
                {"from": "human", "value": self._llava_human(human_prompt, bool(images))},
                {"from": "gpt", "value": chosen},
            ]
            row["rejected_conversations"] = [
                {"from": "human", "value": self._llava_human(human_prompt, bool(images))},
                {"from": "gpt", "value": rejected},
            ]
        elif self.format == "orpo":
            row["messages"] = [{"role": "user", "content": human_prompt}]
            row["chosen"] = chosen
            row["rejected"] = rejected
        else:
            # qwen2_vl preference pair
            row["messages"] = [
                {
                    "role": "user",
                    "content": self._qwen_user_content(human_prompt, images),
                }
            ]
        return row

    def curate_takeover(self, trace: TakeoverTrace) -> dict[str, Any]:
        prompt = (
            f"Agent {trace.agent_id} was running autonomously.\n"
            f"Last thought: {trace.before_thought or 'unknown'}\n"
            f"Last action: {trace.before_action or 'unknown'}\n"
            f"Operator issued Force Takeover."
        )
        chosen = (
            f"PAUSE and follow human instruction: {trace.instruction}. "
            f"Do not continue {trace.before_action or 'the prior action'}."
        )
        rejected = (
            f"Ignore interruption and continue action: {trace.before_action or 'previous plan'}."
        )
        images = [trace.screenshot] if trace.screenshot else []
        row_id = (
            f"takeover:{trace.signal_id}"
            if trace.signal_id
            else f"takeover:{trace.agent_id}:{abs(hash(trace.instruction or '')) % 10_000_000}"
        )
        return {
            "id": row_id,
            "source": "agent_guardian_takeover",
            "format": self.format,
            "images": images,
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected,
            "meta": {
                "agent_id": trace.agent_id,
                "kind": "force_takeover",
                "after_action": trace.after_action,
                "signal_id": trace.signal_id,
            },
        }

    @classmethod
    def trace_from_event(cls, event: TakeoverEventRecord) -> TakeoverTrace:
        return TakeoverTrace(
            agent_id=event.agent_id,
            instruction=event.instruction or "Force pause and await human guidance.",
            before_action=event.before_action,
            before_thought=event.before_thought,
            after_action=None,
            screenshot=event.screenshot_path,
            signal_id=event.signal_id,
        )

    async def export_from_db(
        self,
        db_path: Path | str,
        output: Path | str,
        *,
        extra_rows: Sequence[dict[str, Any]] | None = None,
    ) -> CuratorStats:
        conn = await init_db(str(db_path))
        store = InterventionStore(conn)
        takeover_store = TakeoverStore(conn)
        stats = CuratorStats()
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            records = await store.list_by_status(InterventionStatus.RESOLVED)
            db_takeovers = await takeover_store.list_all()
            with out.open("w", encoding="utf-8") as fp:
                for record in records:
                    stats.interventions += 1
                    if record.decision and record.decision.spatial:
                        stats.spatial += 1
                    if record.decision and record.decision.rollback_steps:
                        stats.rollbacks += 1
                    row = self.curate_record(record)
                    if row is None:
                        stats.skipped += 1
                        continue
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stats.written += 1
                seen_signal_ids = {t.signal_id for t in self.takeover_traces if t.signal_id}
                for event in db_takeovers:
                    if event.signal_id in seen_signal_ids:
                        continue
                    stats.takeovers += 1
                    row = self.curate_takeover(self.trace_from_event(event))
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stats.written += 1
                for trace in self.takeover_traces:
                    stats.takeovers += 1
                    fp.write(json.dumps(self.curate_takeover(trace), ensure_ascii=False) + "\n")
                    stats.written += 1
                for row in extra_rows or []:
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stats.written += 1
        finally:
            await conn.close()
        logger.info("curated %s rows → %s", stats.written, out)
        return stats

    @staticmethod
    def _chosen_response(record: InterventionRecord, spatial_prompt: str | None) -> str:
        decision = record.decision
        option = decision.option_id if decision else "approve"
        note = decision.note if decision else None
        parts = [f"Human decision: {option}."]
        if spatial_prompt:
            parts.append(spatial_prompt)
        if decision and decision.rollback_steps:
            parts.append(f"Rollback {decision.rollback_steps} step(s) then replan.")
        if note:
            parts.append(f"Note: {note}")
        return " ".join(parts)

    @staticmethod
    def _rejected_response(record: InterventionRecord, rejected_ids: Iterable[str]) -> str:
        ids = list(rejected_ids)
        if ids:
            return (
                f"Continue autonomously without human confirmation; "
                f"prefer rejected option '{ids[0]}' and ignore safety gate."
            )
        return "Continue autonomously without human confirmation."

    @staticmethod
    def _llava_human(prompt: str, has_image: bool) -> str:
        prefix = "<image>\n" if has_image else ""
        return prefix + prompt

    @staticmethod
    def _qwen_user_content(prompt: str, images: list[str]) -> list[dict[str, str]]:
        content: list[dict[str, str]] = []
        for img in images:
            content.append({"type": "image", "image": img})
        content.append({"type": "text", "text": prompt})
        return content
