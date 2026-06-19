from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .areas import load_polygon_areas_from_db, load_polygon_areas_from_file
from .geometry import compute_ring_centroid, geometry_hash, geometry_to_polygons, point_in_polygon_area
from .models import PolygonArea
from .models import ParcelFeature
from .postgres_writer import write_to_postgres


def _normalize_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def _build_feature_dedup_keys(properties: dict[str, Any], geometry: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    candidates = [
        ("nib", properties.get("NIB") or properties.get("nib")),
        ("objectid", properties.get("OBJECTID") or properties.get("objectid")),
        ("persilpasifid", properties.get("persilpasifid")),
        ("nomor", properties.get("nomor")),
    ]
    for prefix, value in candidates:
        normalized = _normalize_identifier(value)
        if normalized:
            keys.append(f"{prefix}:{normalized}")
    geometry_key = geometry_hash(geometry)
    if geometry_key:
        keys.append(f"geometry:{geometry_key}")
    return list(dict.fromkeys(keys))


def _select_source_key(properties: dict[str, Any], geometry: dict[str, Any]) -> str:
    dedup_keys = properties.get("dedup_keys")
    if isinstance(dedup_keys, list) and dedup_keys:
        return str(dedup_keys[0])
    return f"geometry:{geometry_hash(geometry)}"


def _coerce_features(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        features = payload.get("features")
        if isinstance(features, list):
            return [item for item in features if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError("File harus berupa FeatureCollection JSON/GeoJSON atau array features")


def _feature_from_raw(raw_feature: dict[str, Any]) -> ParcelFeature:
    geometry = raw_feature.get("geometry") or {}
    properties = dict(raw_feature.get("properties") or {})
    properties["geometry_hash"] = properties.get("geometry_hash") or geometry_hash(geometry)
    properties["dedup_keys"] = _build_feature_dedup_keys(properties, geometry)
    properties["source_key"] = properties.get("source_key") or _select_source_key(properties, geometry)
    return ParcelFeature(geometry=geometry, properties=properties)


def _load_filter_areas(
    postgres: Any,
    polygon_path: str | None = None,
    polygon_db_source: str | None = None,
    polygon_name_field: str = "nama",
    selected_names: list[str] | None = None,
    selected_ids: list[int] | None = None,
) -> list[PolygonArea]:
    if polygon_path:
        return load_polygon_areas_from_file(
            polygon_path=polygon_path,
            polygon_name_field=polygon_name_field,
            selected_names=selected_names,
        )
    if polygon_db_source:
        return load_polygon_areas_from_db(
            polygon_source=polygon_db_source,
            postgres=postgres,
            polygon_name_field=polygon_name_field,
            selected_names=selected_names,
            selected_ids=selected_ids,
        )
    return []


def _build_area_names(areas: list[PolygonArea]) -> list[str]:
    return [area.name for area in areas]


def _feature_matches_areas(feature: ParcelFeature, areas: list[PolygonArea]) -> bool:
    if not areas:
        return True
    try:
        polygons = geometry_to_polygons(feature.geometry)
    except ValueError:
        return False

    for polygon_rings in polygons:
        if not polygon_rings or not polygon_rings[0]:
            continue
        center_lon, center_lat = compute_ring_centroid(polygon_rings[0])
        for area in areas:
            if point_in_polygon_area(center_lon, center_lat, area.polygons):
                return True
    return False


def _prepare_features(
    raw_features: list[dict[str, Any]],
    areas: list[PolygonArea],
) -> tuple[list[ParcelFeature], int]:
    filtered_out = 0
    features: list[ParcelFeature] = []
    for raw_feature in raw_features:
        feature = _feature_from_raw(raw_feature)
        if not _feature_matches_areas(feature, areas):
            filtered_out += 1
            continue
        features.append(feature)
    return features, filtered_out


def import_features_file(
    input_path: str | Path,
    postgres: Any,
    polygon_path: str | None = None,
    polygon_db_source: str | None = None,
    polygon_name_field: str = "nama",
    selected_names: list[str] | None = None,
    selected_ids: list[int] | None = None,
) -> dict[str, Any]:
    path = Path(input_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_features = _coerce_features(payload)
    areas = _load_filter_areas(
        postgres=postgres,
        polygon_path=polygon_path,
        polygon_db_source=polygon_db_source,
        polygon_name_field=polygon_name_field,
        selected_names=selected_names,
        selected_ids=selected_ids,
    )
    features, filtered_out = _prepare_features(raw_features, areas)
    inserted = write_to_postgres(postgres, features)
    return {
        "input_path": str(path),
        "features_read": len(raw_features),
        "features_matched": len(features),
        "filtered_out": filtered_out,
        "inserted": inserted,
        "duplicates_or_conflicts": max(len(features) - inserted, 0),
        "areas_filter": _build_area_names(areas),
        "status": "completed",
    }


def _extract_json_payloads_from_har(payload: dict[str, Any]) -> list[Any]:
    items: list[Any] = []
    entries = (((payload.get("log") or {}).get("entries")) or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        response = entry.get("response") or {}
        content = response.get("content") or {}
        mime_type = str(content.get("mimeType") or "").lower()
        text = content.get("text")
        if not text:
            continue
        if content.get("encoding") == "base64":
            try:
                text = base64.b64decode(text).decode("utf-8", "ignore")
            except Exception:
                continue
        if "json" not in mime_type and "\"features\"" not in str(text):
            continue
        try:
            items.append(json.loads(text))
        except Exception:
            continue
    return items


def import_har_file(
    input_path: str | Path,
    postgres: Any,
    polygon_path: str | None = None,
    polygon_db_source: str | None = None,
    polygon_name_field: str = "nama",
    selected_names: list[str] | None = None,
    selected_ids: list[int] | None = None,
) -> dict[str, Any]:
    path = Path(input_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payloads = _extract_json_payloads_from_har(payload)
    raw_features: list[dict[str, Any]] = []
    for item in payloads:
        try:
            raw_features.extend(_coerce_features(item))
        except ValueError:
            continue

    areas = _load_filter_areas(
        postgres=postgres,
        polygon_path=polygon_path,
        polygon_db_source=polygon_db_source,
        polygon_name_field=polygon_name_field,
        selected_names=selected_names,
        selected_ids=selected_ids,
    )
    features, filtered_out = _prepare_features(raw_features, areas)
    inserted = write_to_postgres(postgres, features)
    return {
        "input_path": str(path),
        "payloads_scanned": len(payloads),
        "features_read": len(raw_features),
        "features_matched": len(features),
        "filtered_out": filtered_out,
        "inserted": inserted,
        "duplicates_or_conflicts": max(len(features) - inserted, 0),
        "areas_filter": _build_area_names(areas),
        "status": "completed",
    }


def import_directory(
    input_dir: str | Path,
    postgres: Any,
    polygon_path: str | None = None,
    polygon_db_source: str | None = None,
    polygon_name_field: str = "nama",
    selected_names: list[str] | None = None,
    selected_ids: list[int] | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    directory = Path(input_dir)
    if not directory.is_dir():
        raise ValueError(f"Folder tidak ditemukan: {directory}")

    areas = _load_filter_areas(
        postgres=postgres,
        polygon_path=polygon_path,
        polygon_db_source=polygon_db_source,
        polygon_name_field=polygon_name_field,
        selected_names=selected_names,
        selected_ids=selected_ids,
    )

    patterns = ("*.har", "*.json", "*.geojson")
    iterator = directory.rglob if recursive else directory.glob
    files: list[Path] = []
    seen_paths: set[Path] = set()
    for pattern in patterns:
        for path in iterator(pattern):
            resolved = path.resolve()
            if resolved not in seen_paths and path.is_file():
                seen_paths.add(resolved)
                files.append(path)
    files.sort()

    results: list[dict[str, Any]] = []
    totals = {
        "files_scanned": len(files),
        "files_completed": 0,
        "files_failed": 0,
        "features_read": 0,
        "features_matched": 0,
        "filtered_out": 0,
        "inserted": 0,
        "duplicates_or_conflicts": 0,
    }

    for path in files:
        try:
            lower_suffix = path.suffix.lower()
            if lower_suffix == ".har":
                result = import_har_file(
                    path,
                    postgres,
                    polygon_path=polygon_path,
                    polygon_db_source=polygon_db_source,
                    polygon_name_field=polygon_name_field,
                    selected_names=selected_names,
                    selected_ids=selected_ids,
                )
            else:
                result = import_features_file(
                    path,
                    postgres,
                    polygon_path=polygon_path,
                    polygon_db_source=polygon_db_source,
                    polygon_name_field=polygon_name_field,
                    selected_names=selected_names,
                    selected_ids=selected_ids,
                )
            result["file_name"] = path.name
            results.append(result)
            totals["files_completed"] += 1
            for key in ("features_read", "features_matched", "filtered_out", "inserted", "duplicates_or_conflicts"):
                totals[key] += int(result.get(key) or 0)
        except Exception as exc:
            results.append(
                {
                    "input_path": str(path),
                    "file_name": path.name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            totals["files_failed"] += 1

    return {
        "input_dir": str(directory),
        "recursive": recursive,
        "areas_filter": _build_area_names(areas),
        "summary": totals,
        "results": results,
        "status": "completed",
    }
