from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import ParcelFeature, RunStats


def write_geojson(path: Path, features: list[ParcelFeature]) -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [feature.to_geojson_feature() for feature in features],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, features: list[ParcelFeature]) -> None:
    fieldnames: list[str] = []
    for feature in features:
        for key in feature.properties.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for feature in features:
            writer.writerow(feature.properties)


def write_summary(path: Path, stats: RunStats, result: dict[str, object]) -> None:
    payload = {
        "points_total": stats.points_total,
        "points_done": stats.points_done,
        "requests_total": stats.requests_total,
        "requests_failed": stats.requests_failed,
        "features_seen": stats.features_seen,
        "features_written": stats.features_written,
        "duplicate_features": stats.duplicate_features,
        "errors": stats.errors,
        "result": result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
