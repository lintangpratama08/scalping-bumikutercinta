from __future__ import annotations

import json
from pathlib import Path

from .config import PostgresConfig
from .geometry import geometry_to_polygons
from .models import PolygonArea


def load_polygon_areas_from_file(
    polygon_path: str,
    polygon_name_field: str = "nama",
    selected_names: list[str] | None = None,
) -> list[PolygonArea]:
    payload = json.loads(Path(polygon_path).read_text(encoding="utf-8"))
    selected_casefold = {name.casefold() for name in selected_names} if selected_names else None
    areas: list[PolygonArea] = []

    for index, raw_feature in enumerate(payload.get("features") or [], start=1):
        properties = raw_feature.get("properties") or {}
        geometry = raw_feature.get("geometry") or {}
        name = (
            properties.get(polygon_name_field)
            or properties.get("WADMKC")
            or properties.get("wadmkc")
            or properties.get("name")
            or f"polygon_{index}"
        )
        if selected_casefold and str(name).casefold() not in selected_casefold:
            continue
        areas.append(
            PolygonArea(
                id=None,
                name=str(name),
                geometry=geometry,
                polygons=geometry_to_polygons(geometry),
                metadata=properties,
            )
        )
    return areas


def load_polygon_areas_from_db(
    polygon_source: str,
    postgres: PostgresConfig,
    polygon_name_field: str = "nama",
    selected_names: list[str] | None = None,
    selected_ids: list[int] | None = None,
) -> list[PolygonArea]:
    table_map = {
        "kecamatan": "data.tb_kecamatan_geo",
        "kelurahan": "data.tb_kelurahan_geo",
    }
    table_name = table_map.get(polygon_source.strip().lower(), polygon_source)
    selected_casefold = {name.casefold() for name in selected_names} if selected_names else None
    selected_ids_set = {int(item) for item in selected_ids} if selected_ids else None
    areas: list[PolygonArea] = []
    import psycopg2

    conn = psycopg2.connect(
        host=postgres.host,
        port=postgres.port,
        user=postgres.user,
        password=postgres.password,
        dbname=postgres.dbname,
    )
    try:
        with conn.cursor() as cur:
            sql = f"SELECT * FROM {table_name}"
            params: list[object] = []
            if selected_ids_set:
                sql += " WHERE id = ANY(%s)"
                params.append(list(selected_ids_set))
            sql += " ORDER BY nama"
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            for index, row in enumerate(cur.fetchall(), start=1):
                item = dict(zip(columns, row))
                name = item.get(polygon_name_field) or item.get("nama") or f"polygon_{index}"
                if selected_casefold and str(name).casefold() not in selected_casefold:
                    continue
                geo_json = item.get("geo_json")
                geometry = geo_json.get("geometry") if isinstance(geo_json, dict) and "geometry" in geo_json else geo_json
                areas.append(
                    PolygonArea(
                        id=int(item["id"]),
                        name=str(name),
                        geometry=geometry,
                        polygons=geometry_to_polygons(geometry),
                        metadata=item,
                    )
                )
    finally:
        conn.close()

    return areas


def get_area_geojson(level: str, area_id: int, postgres: PostgresConfig) -> dict[str, object] | None:
    table_map = {
        "kecamatan": "data.tb_kecamatan_geo",
        "kelurahan": "data.tb_kelurahan_geo",
    }
    if level not in table_map:
        raise ValueError("level harus kecamatan atau kelurahan")
    import psycopg2

    conn = psycopg2.connect(
        host=postgres.host,
        port=postgres.port,
        user=postgres.user,
        password=postgres.password,
        dbname=postgres.dbname,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, nama, geo_json, id_kota, id_kec FROM {table_map[level]} WHERE id = %s LIMIT 1", [area_id])
            row = cur.fetchone()
            if not row:
                return None
            geo_json = row[2]
            geometry = geo_json.get("geometry") if isinstance(geo_json, dict) and "geometry" in geo_json else geo_json
            return {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": {
                            "id": row[0],
                            "nama": row[1],
                            "id_kota": row[3],
                            "id_kec": row[4],
                            "level": level,
                        },
                    }
                ],
            }
    finally:
        conn.close()


def list_areas(level: str, postgres: PostgresConfig, kecamatan_id: int | None = None) -> list[dict[str, object]]:
    table_map = {
        "kecamatan": "SELECT id, nama, id_kota, NULL::integer AS id_kec FROM data.tb_kecamatan_geo",
        "kelurahan": "SELECT id, nama, id_kota, id_kec FROM data.tb_kelurahan_geo",
    }
    if level not in table_map:
        raise ValueError("level harus kecamatan atau kelurahan")
    import psycopg2

    conn = psycopg2.connect(
        host=postgres.host,
        port=postgres.port,
        user=postgres.user,
        password=postgres.password,
        dbname=postgres.dbname,
    )
    try:
        with conn.cursor() as cur:
            sql = table_map[level]
            params: list[object] = []
            if level == "kelurahan" and kecamatan_id is not None:
                sql += " WHERE id_kec = %s"
                params.append(kecamatan_id)
            sql += " ORDER BY nama"
            cur.execute(sql, params)
            return [
                {"id": row[0], "nama": row[1], "id_kota": row[2], "id_kec": row[3]}
                for row in cur.fetchall()
            ]
    finally:
        conn.close()
