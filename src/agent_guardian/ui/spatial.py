"""Convert human spatial marks into multimodal / SoM-style prompts."""

from __future__ import annotations

from agent_guardian.schemas.spatial import SpatialAnnotation


class SpatialPromptInjector:
    """
    Turn Web UI point / bbox annotations into text an agent or VLM can follow.
    """

    @staticmethod
    def to_prompt(annotation: SpatialAnnotation, *, som_id: int | None = 1) -> str:
        label = annotation.label or (
            "click_here" if annotation.type == "point" else "focus_region"
        )
        mark = f"[{som_id}]" if som_id is not None else ""
        unit = "normalized" if annotation.normalized else "px"

        if annotation.type == "point":
            return (
                f"Human spatial guidance {mark}: marked POINT at "
                f"(x={annotation.x:.4f}, y={annotation.y:.4f}) [{unit}] "
                f"with label '{label}'. "
                f"Please perform the intended action at this location "
                f"(Set-of-Mark {mark or label})."
            )

        assert annotation.x2 is not None and annotation.y2 is not None
        cx = (annotation.x + annotation.x2) / 2.0
        cy = (annotation.y + annotation.y2) / 2.0
        return (
            f"Human spatial guidance {mark}: marked BBOX "
            f"[x1={annotation.x:.4f}, y1={annotation.y:.4f}, "
            f"x2={annotation.x2:.4f}, y2={annotation.y2:.4f}] [{unit}] "
            f"center=({cx:.4f}, {cy:.4f}) label='{label}'. "
            f"Restrict the next action to this region "
            f"(Set-of-Mark {mark or label})."
        )

    @staticmethod
    def to_structured(annotation: SpatialAnnotation) -> dict[str, object]:
        """Compact dict for agent tool args / context injection."""
        payload: dict[str, object] = {
            "type": annotation.type,
            "label": annotation.label,
            "normalized": annotation.normalized,
            "x": annotation.x,
            "y": annotation.y,
        }
        if annotation.type == "bbox":
            payload["x2"] = annotation.x2
            payload["y2"] = annotation.y2
            payload["cx"] = (annotation.x + (annotation.x2 or annotation.x)) / 2.0
            payload["cy"] = (annotation.y + (annotation.y2 or annotation.y)) / 2.0
        if annotation.image_width:
            payload["image_width"] = annotation.image_width
        if annotation.image_height:
            payload["image_height"] = annotation.image_height
        return payload
