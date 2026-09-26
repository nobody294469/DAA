"""Geometry and bounding box utilities for document text extraction and evidence grounding.

Conforms to schemas/document_text.json and schemas/evidence.json.
Enforces invariant: x0 <= x1, y0 <= y1.
Supports seamless conversion between [x0, y0, x1, y1] list and {'x0', 'y0', 'x1', 'y1'} dict representations.
"""
from __future__ import annotations

from typing import Any, Sequence, Union
from pydantic import BaseModel, Field, model_validator


class BBoxDict(BaseModel):
    """Bounding box dictionary representation matching schemas/document_text.json."""
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_coordinates(self) -> BBoxDict:
        if self.x0 > self.x1:
            raise ValueError(f"Invalid bounding box: x0 ({self.x0}) > x1 ({self.x1})")
        if self.y0 > self.y1:
            raise ValueError(f"Invalid bounding box: y0 ({self.y0}) > y1 ({self.y1})")
        return self

    def to_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


class BBox:
    """Canonical Bounding Box utility class."""

    __slots__ = ("_x0", "_y0", "_x1", "_y1")

    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        if x0 > x1:
            raise ValueError(f"Invalid bounding box: x0 ({x0}) > x1 ({x1})")
        if y0 > y1:
            raise ValueError(f"Invalid bounding box: y0 ({y0}) > y1 ({y1})")
        self._x0 = round(float(x0), 2)
        self._y0 = round(float(y0), 2)
        self._x1 = round(float(x1), 2)
        self._y1 = round(float(y1), 2)

    @property
    def x0(self) -> float:
        return self._x0

    @property
    def y0(self) -> float:
        return self._y0

    @property
    def x1(self) -> float:
        return self._x1

    @property
    def y1(self) -> float:
        return self._y1

    @property
    def width(self) -> float:
        return round(self._x1 - self._x0, 2)

    @property
    def height(self) -> float:
        return round(self._y1 - self._y0, 2)

    @property
    def area(self) -> float:
        return round(self.width * self.height, 2)

    def to_list(self) -> list[float]:
        return [self._x0, self._y0, self._x1, self._y1]

    def to_dict(self) -> dict[str, float]:
        return {"x0": self._x0, "y0": self._y0, "x1": self._x1, "y1": self._y1}

    def to_bbox_dict(self) -> BBoxDict:
        return BBoxDict(x0=self._x0, y0=self._y0, x1=self._x1, y1=self._y1)

    @classmethod
    def from_any(cls, obj: Any) -> BBox:
        """Parse BBox from sequence [x0, y0, x1, y1], dict {'x0', 'y0', 'x1', 'y1'}, or BBox."""
        if isinstance(obj, BBox):
            return obj
        if isinstance(obj, BBoxDict):
            return cls(obj.x0, obj.y0, obj.x1, obj.y1)
        if isinstance(obj, dict):
            if "x0" in obj and "y0" in obj and "x1" in obj and "y1" in obj:
                return cls(obj["x0"], obj["y0"], obj["x1"], obj["y1"])
            raise ValueError(f"Dict missing required bbox keys (x0, y0, x1, y1): {obj}")
        if isinstance(obj, (list, tuple)):
            if len(obj) == 4:
                return cls(obj[0], obj[1], obj[2], obj[3])
            raise ValueError(f"Sequence must have length 4, got length {len(obj)}: {obj}")
        raise TypeError(f"Cannot parse BBox from type {type(obj)}: {obj}")

    def union(self, other: Any) -> BBox:
        """Compute the smallest bounding box enclosing self and other."""
        o = BBox.from_any(other)
        return BBox(
            min(self._x0, o.x0),
            min(self._y0, o.y0),
            max(self._x1, o.x1),
            max(self._y1, o.y1),
        )

    def intersection(self, other: Any) -> BBox | None:
        """Compute intersection with another bounding box, or None if disjoint."""
        o = BBox.from_any(other)
        ix0 = max(self._x0, o.x0)
        iy0 = max(self._y0, o.y0)
        ix1 = min(self._x1, o.x1)
        iy1 = min(self._y1, o.y1)
        if ix0 <= ix1 and iy0 <= iy1:
            return BBox(ix0, iy0, ix1, iy1)
        return None

    def iou(self, other: Any) -> float:
        """Compute Intersection over Union (IoU) with another box."""
        inter = self.intersection(other)
        if inter is None or inter.area == 0.0:
            return 0.0
        o = BBox.from_any(other)
        union_area = self.area + o.area - inter.area
        if union_area <= 0.0:
            return 0.0
        return round(inter.area / union_area, 4)

    def contains_point(self, x: float, y: float) -> bool:
        return self._x0 <= x <= self._x1 and self._y0 <= y <= self._y1

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, (BBox, BBoxDict, list, tuple, dict)):
            return False
        try:
            o = BBox.from_any(other)
            return (self._x0, self._y0, self._x1, self._y1) == (o.x0, o.y0, o.x1, o.y1)
        except Exception:
            return False

    def __repr__(self) -> str:
        return f"BBox(x0={self._x0}, y0={self._y0}, x1={self._x1}, y1={self._y1})"


def enclose_bboxes(bboxes: Sequence[Any]) -> BBox | None:
    """Return bounding box enclosing all valid bounding boxes in the sequence."""
    valid_boxes = []
    for b in bboxes:
        if b is not None:
            try:
                valid_boxes.append(BBox.from_any(b))
            except Exception:
                pass
    if not valid_boxes:
        return None
    res = valid_boxes[0]
    for b in valid_boxes[1:]:
        res = res.union(b)
    return res
