"""Phase 6 spatial annotations for visual steering."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpatialAnnotationType(StrEnum):
    POINT = "point"
    BBOX = "bbox"


class SpatialAnnotation(BaseModel):
    """
    Human mark on an agent screenshot.

    Coordinates are normalized to image size in [0, 1] when ``normalized=True``
    (recommended). Absolute pixel values when ``normalized=False``.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["point", "bbox"]
    x: float = Field(description="Point x, or bbox left")
    y: float = Field(description="Point y, or bbox top")
    x2: float | None = Field(default=None, description="BBox right")
    y2: float | None = Field(default=None, description="BBox bottom")
    label: str | None = Field(default=None, max_length=128)
    normalized: bool = True
    image_width: int | None = Field(default=None, ge=1)
    image_height: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_shape(self) -> SpatialAnnotation:
        if self.type == "bbox":
            if self.x2 is None or self.y2 is None:
                raise ValueError("bbox requires x2 and y2")
            if self.x2 < self.x or self.y2 < self.y:
                raise ValueError("bbox requires x2>=x and y2>=y")
        return self
