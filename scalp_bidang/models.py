from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolygonArea:
    id: int | None
    name: str
    geometry: dict[str, Any]
    polygons: list[list[list[tuple[float, float]]]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SamplePoint:
    area_name: str
    longitude: float
    latitude: float


@dataclass
class ParcelFeature:
    geometry: dict[str, Any]
    properties: dict[str, Any]

    def to_geojson_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": self.geometry,
            "properties": self.properties,
        }


@dataclass
class RunStats:
    points_total: int = 0
    points_done: int = 0
    requests_total: int = 0
    requests_failed: int = 0
    features_seen: int = 0
    features_written: int = 0
    duplicate_features: int = 0
    errors: list[str] = field(default_factory=list)
