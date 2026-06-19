from __future__ import annotations

import hashlib
import json
import math
from typing import Any


WGS84_RADIUS = 6378137.0


def lonlat_to_web_mercator(longitude: float, latitude: float) -> tuple[float, float]:
    x = math.radians(longitude) * WGS84_RADIUS
    clamped_latitude = max(min(latitude, 85.05112878), -85.05112878)
    y = WGS84_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(clamped_latitude) / 2))
    return x, y


def web_mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    longitude = math.degrees(x / WGS84_RADIUS)
    latitude = math.degrees(2 * math.atan(math.exp(y / WGS84_RADIUS)) - math.pi / 2)
    return longitude, latitude


def point_in_ring(longitude: float, latitude: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False

    prev_lon, prev_lat = ring[-1]
    for curr_lon, curr_lat in ring:
        intersects = ((curr_lat > latitude) != (prev_lat > latitude)) and (
            longitude < (prev_lon - curr_lon) * (latitude - curr_lat) / ((prev_lat - curr_lat) or 1e-12) + curr_lon
        )
        if intersects:
            inside = not inside
        prev_lon, prev_lat = curr_lon, curr_lat
    return inside


def point_in_polygon(longitude: float, latitude: float, rings: list[list[tuple[float, float]]]) -> bool:
    if not rings:
        return False
    if not point_in_ring(longitude, latitude, rings[0]):
        return False
    for hole in rings[1:]:
        if point_in_ring(longitude, latitude, hole):
            return False
    return True


def point_in_polygon_area(
    longitude: float,
    latitude: float,
    polygons: list[list[list[tuple[float, float]]]],
) -> bool:
    return any(point_in_polygon(longitude, latitude, rings) for rings in polygons)


def compute_ring_centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    total_lon = sum(point[0] for point in ring)
    total_lat = sum(point[1] for point in ring)
    return total_lon / len(ring), total_lat / len(ring)


def compute_polygon_radius_meters(ring: list[tuple[float, float]], center_lon: float, center_lat: float) -> float:
    center_x, center_y = lonlat_to_web_mercator(center_lon, center_lat)
    max_distance = 0.0
    for longitude, latitude in ring:
        point_x, point_y = lonlat_to_web_mercator(longitude, latitude)
        max_distance = max(max_distance, math.hypot(point_x - center_x, point_y - center_y))
    return max_distance or 1.0


def move_point_towards(
    longitude: float,
    latitude: float,
    target_longitude: float,
    target_latitude: float,
    ratio: float,
) -> tuple[float, float]:
    ratio = max(0.0, min(1.0, ratio))
    source_x, source_y = lonlat_to_web_mercator(longitude, latitude)
    target_x, target_y = lonlat_to_web_mercator(target_longitude, target_latitude)
    return web_mercator_to_lonlat(
        source_x + ((target_x - source_x) * ratio),
        source_y + ((target_y - source_y) * ratio),
    )


def sample_ring_points(
    ring: list[tuple[float, float]],
    spacing_meters: float,
) -> list[tuple[float, float]]:
    if len(ring) < 2:
        return list(ring)

    sampled: list[tuple[float, float]] = []
    closed_ring = list(ring)
    if closed_ring[0] != closed_ring[-1]:
        closed_ring.append(closed_ring[0])

    for start, end in zip(closed_ring, closed_ring[1:]):
        start_x, start_y = lonlat_to_web_mercator(*start)
        end_x, end_y = lonlat_to_web_mercator(*end)
        segment_length = math.hypot(end_x - start_x, end_y - start_y)
        steps = max(1, int(math.ceil(segment_length / max(spacing_meters, 1.0))))
        for index in range(steps):
            ratio = index / steps
            point_x = start_x + ((end_x - start_x) * ratio)
            point_y = start_y + ((end_y - start_y) * ratio)
            sampled.append(web_mercator_to_lonlat(point_x, point_y))

    sampled.append(closed_ring[-1])
    return sampled


def geometry_to_rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    if geometry_type == "Polygon":
        return [[(float(point[0]), float(point[1])) for point in ring] for ring in coordinates]

    if geometry_type == "MultiPolygon":
        rings: list[list[tuple[float, float]]] = []
        for polygon in coordinates:
            rings.extend([[(float(point[0]), float(point[1])) for point in ring] for ring in polygon])
        return rings

    raise ValueError(f"Tipe geometry tidak didukung: {geometry_type}")


def geometry_to_polygons(geometry: dict[str, Any]) -> list[list[list[tuple[float, float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    if geometry_type == "Polygon":
        return [[[(float(point[0]), float(point[1])) for point in ring] for ring in coordinates]]

    if geometry_type == "MultiPolygon":
        return [
            [[(float(point[0]), float(point[1])) for point in ring] for ring in polygon]
            for polygon in coordinates
        ]

    raise ValueError(f"Tipe geometry tidak didukung: {geometry_type}")


def compute_bounds(ring: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    longitudes = [point[0] for point in ring]
    latitudes = [point[1] for point in ring]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def build_getfeatureinfo_url(
    longitude: float,
    latitude: float,
    half_size_meters: float,
    feature_count: int,
) -> str:
    x, y = lonlat_to_web_mercator(longitude, latitude)
    xmin = x - half_size_meters
    ymin = y - half_size_meters
    xmax = x + half_size_meters
    ymax = y + half_size_meters
    return (
        "https://bhumi.atrbpn.go.id/mprx/service"
        "?SERVICE=WMS"
        "&VERSION=1.3.0"
        "&REQUEST=GetFeatureInfo"
        "&LAYERS=bhumi_persil"
        "&QUERY_LAYERS=bhumi_persil"
        "&STYLES="
        "&CRS=EPSG:3857"
        f"&BBOX={xmin},{ymin},{xmax},{ymax}"
        "&WIDTH=256"
        "&HEIGHT=256"
        "&I=128"
        "&J=128"
        "&INFO_FORMAT=application/json"
        f"&FEATURE_COUNT={feature_count}"
    )


def _polygon_to_wkt(coordinates: list[list[list[float]]]) -> str:
    rings = []
    for ring in coordinates:
        points = ", ".join(f"{point[0]} {point[1]}" for point in ring)
        rings.append(f"({points})")
    return f"({', '.join(rings)})"


def geometry_to_wkt(geometry: dict[str, Any]) -> str | None:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon" and coordinates:
        return f"MULTIPOLYGON ({_polygon_to_wkt(coordinates)})"

    if geometry_type == "MultiPolygon" and coordinates:
        polygons = [_polygon_to_wkt(polygon) for polygon in coordinates]
        return f"MULTIPOLYGON ({', '.join(polygons)})"

    return None


def geometry_hash(geometry: dict[str, Any]) -> str:
    payload = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def normalize_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def is_bbox_like_geometry(geometry: dict[str, Any]) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    polygons: list[list[list[float]]] = []
    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        return False

    if len(polygons) != 1:
        return False
    rings = polygons[0]
    if len(rings) != 1:
        return False
    ring = rings[0]
    if len(ring) != 5:
        return False

    points = ring[:-1]
    if ring[0] != ring[-1]:
        return False

    xs = sorted({round(float(point[0]), 12) for point in points})
    ys = sorted({round(float(point[1]), 12) for point in points})
    if len(xs) != 2 or len(ys) != 2:
        return False

    expected = {
        (xs[0], ys[0]),
        (xs[0], ys[1]),
        (xs[1], ys[0]),
        (xs[1], ys[1]),
    }
    actual = {(round(float(point[0]), 12), round(float(point[1]), 12)) for point in points}
    return actual == expected
