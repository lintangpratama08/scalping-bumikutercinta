from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .areas import load_polygon_areas_from_db, load_polygon_areas_from_file
from .config import DEFAULT_HEADERS, HttpConfig, PostgresConfig
from .exporters import write_csv, write_geojson, write_summary
from .geometry import (
    build_getfeatureinfo_url,
    compute_bounds,
    compute_ring_centroid,
    geometry_to_polygons,
    geometry_hash,
    lonlat_to_web_mercator,
    move_point_towards,
    normalize_identifier,
    point_in_polygon_area,
    sample_ring_points,
    web_mercator_to_lonlat,
)
from .models import ParcelFeature, PolygonArea, RunStats, SamplePoint
from .postgres_writer import load_existing_hashes, write_to_postgres


@dataclass(frozen=True)
class CoveragePreset:
    spacing_meters: float
    query_half_size_meters: float
    feature_count: int
    repeat_passes: int
    concurrency: int
    offsets: tuple[tuple[float, float], ...]
    perimeter_spacing_factor: float = 0.75
    perimeter_pull_ratio: float = 0.18
    adaptive_seed_spacing_meters: float = 18.0
    adaptive_pull_ratio: float = 0.24


COVERAGE_PRESETS = {
    "balanced": CoveragePreset(
        120.0,
        26.0,
        28,
        1,
        10,
        ((0.0, 0.0), (0.5, 0.5)),
    ),
    "aggressive": CoveragePreset(
        70.0,
        30.0,
        60,
        2,
        16,
        ((0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5)),
    ),
    "saturation": CoveragePreset(
        42.0,
        34.0,
        100,
        3,
        20,
        (
            (0.0, 0.0), (0.33, 0.0), (0.66, 0.0),
            (0.0, 0.33), (0.33, 0.33), (0.66, 0.33),
            (0.0, 0.66), (0.33, 0.66), (0.66, 0.66),
        ),
    ),
    "bhumi-full": CoveragePreset(
        24.0,
        38.0,
        140,
        4,
        24,
        (
            (0.00, 0.00), (0.25, 0.00), (0.50, 0.00), (0.75, 0.00),
            (0.00, 0.25), (0.25, 0.25), (0.50, 0.25), (0.75, 0.25),
            (0.00, 0.50), (0.25, 0.50), (0.50, 0.50), (0.75, 0.50),
            (0.00, 0.75), (0.25, 0.75), (0.50, 0.75), (0.75, 0.75),
        ),
    ),
    "overpower": CoveragePreset(
        16.0,
        48.0,
        220,
        5,
        28,
        (
            (0.00, 0.00), (0.20, 0.00), (0.40, 0.00), (0.60, 0.00), (0.80, 0.00),
            (0.00, 0.20), (0.20, 0.20), (0.40, 0.20), (0.60, 0.20), (0.80, 0.20),
            (0.00, 0.40), (0.20, 0.40), (0.40, 0.40), (0.60, 0.40), (0.80, 0.40),
            (0.00, 0.60), (0.20, 0.60), (0.40, 0.60), (0.60, 0.60), (0.80, 0.60),
            (0.00, 0.80), (0.20, 0.80), (0.40, 0.80), (0.60, 0.80), (0.80, 0.80),
        ),
        perimeter_spacing_factor=0.45,
        perimeter_pull_ratio=0.22,
        adaptive_seed_spacing_meters=10.0,
        adaptive_pull_ratio=0.30,
    ),
}


def append_sample_point(
    points: list[SamplePoint],
    seen_points: set[tuple[str, float, float]],
    area_name: str,
    longitude: float,
    latitude: float,
    front: bool = False,
) -> bool:
    rounded_lon = round(longitude, 7)
    rounded_lat = round(latitude, 7)
    key = (area_name, rounded_lon, rounded_lat)
    if key in seen_points:
        return False
    seen_points.add(key)
    sample = SamplePoint(area_name=area_name, longitude=rounded_lon, latitude=rounded_lat)
    if front:
        points.insert(0, sample)
    else:
        points.append(sample)
    return True


def build_sample_points(
    areas: list[PolygonArea],
    spacing_meters: float,
    offsets: tuple[tuple[float, float], ...],
    perimeter_spacing_factor: float = 0.75,
    perimeter_pull_ratio: float = 0.18,
) -> list[SamplePoint]:
    points: list[SamplePoint] = []
    seen_points: set[tuple[str, float, float]] = set()
    for area in areas:
        if not area.polygons:
            continue
        polygon_centers: list[tuple[float, float]] = []

        for polygon_rings in area.polygons:
            if not polygon_rings:
                continue
            outer_ring = polygon_rings[0]
            west, south, east, north = compute_bounds(outer_ring)
            center_lon, center_lat = compute_ring_centroid(outer_ring)
            polygon_centers.append((center_lon, center_lat))
            west_x, south_y = lonlat_to_web_mercator(west, south)
            east_x, north_y = lonlat_to_web_mercator(east, north)
            min_x, max_x = sorted((west_x, east_x))
            min_y, max_y = sorted((south_y, north_y))

            for offset_x_factor, offset_y_factor in offsets:
                x = min_x + (spacing_meters * offset_x_factor)
                while x <= max_x + 1e-9:
                    y = min_y + (spacing_meters * offset_y_factor)
                    while y <= max_y + 1e-9:
                        longitude, latitude = web_mercator_to_lonlat(x, y)
                        if point_in_polygon_area(longitude, latitude, [polygon_rings]):
                            append_sample_point(points, seen_points, area.name, longitude, latitude)
                        y += spacing_meters
                    x += spacing_meters

            perimeter_spacing = max(spacing_meters * perimeter_spacing_factor, 6.0)
            for edge_lon, edge_lat in sample_ring_points(outer_ring, perimeter_spacing):
                inner_lon, inner_lat = move_point_towards(
                    edge_lon,
                    edge_lat,
                    center_lon,
                    center_lat,
                    perimeter_pull_ratio,
                )
                append_sample_point(points, seen_points, area.name, inner_lon, inner_lat)

        # Selalu tambah centroid tiap komponen agar area besar cepat punya titik awal bernilai.
        for center_lon, center_lat in polygon_centers:
            append_sample_point(points, seen_points, area.name, center_lon, center_lat, front=True)
    return points


def build_adaptive_sample_points(
    area: PolygonArea,
    features: list[ParcelFeature],
    spacing_meters: float,
    pull_ratio: float,
    seen_points: set[tuple[str, float, float]],
) -> list[SamplePoint]:
    adaptive_points: list[SamplePoint] = []

    for feature in features:
        geometry = feature.geometry or {}
        try:
            polygons = geometry_to_polygons(geometry)
        except ValueError:
            continue

        for polygon_rings in polygons:
            if not polygon_rings:
                continue
            outer_ring = polygon_rings[0]
            center_lon, center_lat = compute_ring_centroid(outer_ring)
            append_sample_point(adaptive_points, seen_points, area.name, center_lon, center_lat)

            seed_spacing = max(spacing_meters, 6.0)
            for edge_lon, edge_lat in sample_ring_points(outer_ring, seed_spacing):
                inner_lon, inner_lat = move_point_towards(
                    edge_lon,
                    edge_lat,
                    center_lon,
                    center_lat,
                    pull_ratio,
                )
                if point_in_polygon_area(inner_lon, inner_lat, area.polygons):
                    append_sample_point(adaptive_points, seen_points, area.name, inner_lon, inner_lat)

    return adaptive_points


def normalize_feature(raw_feature: dict[str, Any], point: SamplePoint, area: PolygonArea) -> ParcelFeature:
    properties = dict(raw_feature.get("properties") or {})
    properties["sample_source"] = point.area_name
    properties["sample_lon"] = point.longitude
    properties["sample_lat"] = point.latitude
    properties["kecamatan_id"] = area.metadata.get("id") if area.metadata.get("id_kec") is None else area.metadata.get("id_kec")
    properties["kecamatan_nama"] = area.metadata.get("nama") if area.metadata.get("id_kec") is None else None
    properties["kelurahan_id"] = area.metadata.get("id") if area.metadata.get("id_kec") is not None else None
    properties["kelurahan_nama"] = area.metadata.get("nama") if area.metadata.get("id_kec") is not None else None
    geometry = raw_feature.get("geometry") or {}
    properties["geometry_hash"] = geometry_hash(geometry)
    dedup_keys = build_feature_dedup_keys(properties, geometry)
    properties["dedup_keys"] = dedup_keys
    properties["source_key"] = select_source_key(properties, geometry)
    return ParcelFeature(geometry=geometry, properties=properties)


def build_feature_dedup_keys(properties: dict[str, Any], geometry: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    candidates = [
        ("nib", properties.get("NIB") or properties.get("nib")),
        ("objectid", properties.get("OBJECTID") or properties.get("objectid")),
        ("persilpasifid", properties.get("persilpasifid")),
        ("nomor", properties.get("nomor")),
    ]
    for prefix, value in candidates:
        normalized = normalize_identifier(value)
        if normalized:
            keys.append(f"{prefix}:{normalized}")
    geometry_key = geometry_hash(geometry)
    if geometry_key:
        keys.append(f"geometry:{geometry_key}")
    return list(dict.fromkeys(keys))


def select_source_key(properties: dict[str, Any], geometry: dict[str, Any]) -> str:
    dedup_keys = properties.get("dedup_keys")
    if isinstance(dedup_keys, list) and dedup_keys:
        return str(dedup_keys[0])
    return f"geometry:{geometry_hash(geometry)}"


async def fetch_payload(
    client: httpx.AsyncClient,
    point: SamplePoint,
    query_half_size_meters: float,
    feature_count: int,
) -> dict[str, Any]:
    query_profiles = [
        (query_half_size_meters, feature_count),
        (query_half_size_meters * 1.2, max(feature_count, 90)),
        (query_half_size_meters * 1.45, max(feature_count + 80, int(feature_count * 1.4))),
        (query_half_size_meters * 0.7, max(30, feature_count // 2)),
        (query_half_size_meters * 0.45, max(20, feature_count // 3)),
    ]
    last_error: Exception | None = None

    for half_size, features_limit in query_profiles:
        url = build_getfeatureinfo_url(
            point.longitude,
            point.latitude,
            half_size_meters=half_size,
            feature_count=features_limit,
        )
        for attempt in range(3):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.4 * (attempt + 1))

    if last_error:
        raise last_error
    return {"type": "FeatureCollection", "features": []}


async def scrape_points(
    points: list[SamplePoint],
    area: PolygonArea,
    http_config: HttpConfig,
    query_half_size_meters: float,
    feature_count: int,
    repeat_passes: int,
    adaptive_seed_spacing_meters: float,
    adaptive_pull_ratio: float,
    limit: int,
    existing_hashes: set[str] | None = None,
    on_chunk_complete: Callable[[RunStats, list[ParcelFeature]], None] | None = None,
) -> tuple[list[ParcelFeature], RunStats]:
    seen = set(existing_hashes or set())
    seen_point_keys = {(point.area_name, point.longitude, point.latitude) for point in points}
    stats = RunStats(points_total=0)
    collected: list[ParcelFeature] = []
    pending_chunk_features: list[ParcelFeature] = []
    semaphore = asyncio.Semaphore(http_config.concurrency)
    limits = httpx.Limits(
        max_connections=http_config.connect_limit,
        max_keepalive_connections=http_config.keepalive_limit,
    )

    async with httpx.AsyncClient(
        timeout=http_config.timeout_seconds,
        headers=DEFAULT_HEADERS,
        verify=False,
        limits=limits,
        http2=False,
    ) as client:
        async def worker(point: SamplePoint) -> None:
            async with semaphore:
                stats.requests_total += 1
                try:
                    payload = await fetch_payload(client, point, query_half_size_meters, feature_count)
                except Exception as exc:
                    stats.requests_failed += 1
                    stats.points_done += 1
                    stats.errors.append(f"{point.area_name} ({point.longitude},{point.latitude}): {exc}")
                    return

                features = payload.get("features") or []
                stats.features_seen += len(features)
                for raw_feature in features:
                    feature = normalize_feature(raw_feature, point, area)
                    dedup_keys = feature.properties.get("dedup_keys") or [f"geometry:{feature.properties['geometry_hash']}"]
                    if any(key in seen for key in dedup_keys):
                        stats.duplicate_features += 1
                        continue
                    seen.update(dedup_keys)
                    collected.append(feature)
                    pending_chunk_features.append(feature)
                    stats.features_written += 1
                    if limit > 0 and len(collected) >= limit:
                        break
                stats.points_done += 1

        chunk_size = max(http_config.concurrency * 3, 24)
        pass_points = list(points)
        for _pass in range(repeat_passes):
            if not pass_points:
                break
            stats.points_total += len(pass_points)
            pass_feature_start = len(collected)
            for start_index in range(0, len(pass_points), chunk_size):
                if limit > 0 and len(collected) >= limit:
                    break
                chunk_points = pass_points[start_index:start_index + chunk_size]
                tasks = [asyncio.create_task(worker(point)) for point in chunk_points]
                if tasks:
                    await asyncio.gather(*tasks)
                if on_chunk_complete:
                    flushed_features = list(pending_chunk_features)
                    on_chunk_complete(stats, flushed_features)
                    pending_chunk_features.clear()
            if limit > 0 and len(collected) >= limit:
                break
            newly_collected = collected[pass_feature_start:]
            pass_points = build_adaptive_sample_points(
                area=area,
                features=newly_collected,
                spacing_meters=adaptive_seed_spacing_meters,
                pull_ratio=adaptive_pull_ratio,
                seen_points=seen_point_keys,
            )

    return collected, stats


def load_areas(
    polygon_path: str | None,
    polygon_db_source: str | None,
    polygon_name_field: str,
    selected_names: list[str] | None,
    selected_ids: list[int] | None,
    postgres: PostgresConfig,
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
    raise ValueError("Salah satu dari polygon_path atau polygon_db_source wajib diisi")


def write_latest_run(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / "latest_run.json"
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_scrape(
    polygon_path: str | None,
    polygon_db_source: str | None,
    polygon_name_field: str,
    selected_names: list[str] | None,
    selected_ids: list[int] | None,
    coverage: str,
    limit: int,
    limit_per_area: int,
    export_files: bool,
    output_dir: Path,
    output_name: str,
    postgres_enabled: bool,
    skip_existing: bool,
    postgres: PostgresConfig,
    http_config: HttpConfig,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    preset = COVERAGE_PRESETS[coverage]
    tuned_http = HttpConfig(
        timeout_seconds=http_config.timeout_seconds,
        concurrency=max(http_config.concurrency, preset.concurrency),
        connect_limit=max(http_config.connect_limit, preset.concurrency * 4),
        keepalive_limit=max(http_config.keepalive_limit, preset.concurrency * 2),
    )
    areas = load_areas(
        polygon_path=polygon_path,
        polygon_db_source=polygon_db_source,
        polygon_name_field=polygon_name_field,
        selected_names=selected_names,
        selected_ids=selected_ids,
        postgres=postgres,
    )
    if not areas:
        raise ValueError("Tidak ada polygon area yang ditemukan")

    total_features = 0
    total_inserted = 0
    per_area_results: list[dict[str, Any]] = []

    for area in areas:
        points = build_sample_points(
            [area],
            preset.spacing_meters,
            preset.offsets,
            perimeter_spacing_factor=preset.perimeter_spacing_factor,
            perimeter_pull_ratio=preset.perimeter_pull_ratio,
        )
        existing_hashes = load_existing_hashes(postgres, area.name) if (postgres_enabled and skip_existing) else set()
        area_limit = limit if limit > 0 else limit_per_area
        inserted_so_far = 0
        area_result: dict[str, Any] = {
            "area_id": area.id,
            "area_level": polygon_db_source or "file",
            "area": area.name,
            "points_total": 0,
            "points_done": 0,
            "requests_total": 0,
            "requests_failed": 0,
            "features": 0,
            "inserted": 0,
            "duplicates": 0,
            "existing_hashes": len(existing_hashes),
            "errors": [],
            "outputs": {},
            "status": "running",
        }
        per_area_results.append(area_result)

        def emit_progress(stats: RunStats, new_features: list[ParcelFeature]) -> None:
            nonlocal inserted_so_far, total_inserted
            inserted_increment = 0
            if postgres_enabled and new_features:
                inserted_increment = write_to_postgres(postgres, new_features)
                inserted_so_far += inserted_increment
                total_inserted += inserted_increment
            area_result.update(
                {
                    "points_total": stats.points_total,
                    "points_done": stats.points_done,
                    "requests_total": stats.requests_total,
                    "requests_failed": stats.requests_failed,
                    "features": stats.features_written,
                    "inserted": inserted_so_far,
                    "duplicates": stats.duplicate_features,
                    "errors": list(stats.errors[-10:]),
                }
            )
            if progress_callback:
                progress_callback(
                    {
                        "areas": len(areas),
                        "features_total": total_features + stats.features_written,
                        "inserted_total": total_inserted,
                        "coverage": coverage,
                        "spacing_meters": preset.spacing_meters,
                        "query_half_size_meters": preset.query_half_size_meters,
                        "feature_count": preset.feature_count,
                        "repeat_passes": preset.repeat_passes,
                        "concurrency": tuned_http.concurrency,
                        "results": per_area_results,
                        "current_area_id": area.id,
                        "current_area_name": area.name,
                        "status": "running",
                    }
                )

        features, stats = asyncio.run(
            scrape_points(
                points=points,
                area=area,
                http_config=tuned_http,
                query_half_size_meters=preset.query_half_size_meters,
                feature_count=preset.feature_count,
                repeat_passes=preset.repeat_passes,
                adaptive_seed_spacing_meters=preset.adaptive_seed_spacing_meters,
                adaptive_pull_ratio=preset.adaptive_pull_ratio,
                limit=area_limit,
                existing_hashes=existing_hashes,
                on_chunk_complete=emit_progress,
            )
        )
        inserted = inserted_so_far
        total_features += len(features)
        area_result.update(
            {
                "points_total": stats.points_total,
                "points_done": stats.points_done,
                "requests_total": stats.requests_total,
                "requests_failed": stats.requests_failed,
                "features": len(features),
                "inserted": inserted,
                "duplicates": stats.duplicate_features,
                "existing_hashes": len(existing_hashes),
                "errors": stats.errors,
                "status": "completed",
            }
        )

        if export_files:
            output_dir.mkdir(parents=True, exist_ok=True)
            area_slug = area.name.lower().replace(" ", "_")
            geojson_path = output_dir / f"{output_name}_{area_slug}.geojson"
            csv_path = output_dir / f"{output_name}_{area_slug}.csv"
            summary_path = output_dir / f"{output_name}_{area_slug}.summary.json"
            write_geojson(geojson_path, features)
            write_csv(csv_path, features)
            write_summary(summary_path, stats, area_result)
            area_result["outputs"] = {
                "geojson": str(geojson_path),
                "csv": str(csv_path),
                "summary": str(summary_path),
            }

    result = {
        "areas": len(areas),
        "features_total": total_features,
        "inserted_total": total_inserted,
        "coverage": coverage,
        "spacing_meters": preset.spacing_meters,
        "query_half_size_meters": preset.query_half_size_meters,
        "feature_count": preset.feature_count,
        "repeat_passes": preset.repeat_passes,
        "concurrency": tuned_http.concurrency,
        "sampling_offsets": [list(item) for item in preset.offsets],
        "results": per_area_results,
        "status": "completed",
    }
    write_latest_run(output_dir, result)
    if progress_callback:
        progress_callback(result)
    return result
