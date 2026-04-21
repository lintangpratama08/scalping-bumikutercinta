from __future__ import annotations

from typing import Any

from .config import PostgresConfig
from .geometry import geometry_hash, geometry_to_wkt, normalize_identifier
from .models import ParcelFeature


def _to_bigint_or_none(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def load_existing_hashes(postgres: PostgresConfig, area_name: str | None = None) -> set[str]:
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
            sql = [
                "SELECT geometry_hash, nib, objectid, persilpasifid, nomor",
                f"FROM {postgres.table}",
                "WHERE source_layer = %s",
            ]
            params: list[object] = [postgres.source_layer]
            if area_name:
                sql.append("AND raw_properties->>'sample_source' = %s")
                params.append(area_name)
            cur.execute(" ".join(sql), params)
            keys: set[str] = set()
            for geometry_hash_value, nib, objectid, persilpasifid, nomor in cur.fetchall():
                if normalize_identifier(nib):
                    keys.add(f"nib:{normalize_identifier(nib)}")
                if normalize_identifier(objectid):
                    keys.add(f"objectid:{normalize_identifier(objectid)}")
                if normalize_identifier(persilpasifid):
                    keys.add(f"persilpasifid:{normalize_identifier(persilpasifid)}")
                if normalize_identifier(nomor):
                    keys.add(f"nomor:{normalize_identifier(nomor)}")
                if normalize_identifier(geometry_hash_value):
                    keys.add(f"geometry:{normalize_identifier(geometry_hash_value)}")
            return keys
    finally:
        conn.close()


def write_to_postgres(postgres: PostgresConfig, features: list[ParcelFeature]) -> int:
    if not features:
        return 0
    import psycopg2
    from psycopg2.extras import Json, execute_values

    conn = psycopg2.connect(
        host=postgres.host,
        port=postgres.port,
        user=postgres.user,
        password=postgres.password,
        dbname=postgres.dbname,
    )
    inserted = 0
    rows: list[tuple[Any, ...]] = []
    batch_seen: set[str] = set()

    for feature in features:
        props = feature.properties
        wkt = geometry_to_wkt(feature.geometry)
        hash_value = props.get("geometry_hash") or geometry_hash(feature.geometry)
        dedup_keys = [str(item) for item in (props.get("dedup_keys") or []) if item]
        if dedup_keys and any(item in batch_seen for item in dedup_keys):
            continue
        batch_seen.update(dedup_keys or [f"geometry:{hash_value}"])
        source_key = props.get("source_key") or (dedup_keys[0] if dedup_keys else f"geometry:{hash_value}")
        rows.append(
            (
                source_key,
                hash_value,
                props.get("NIB") or props.get("nib"),
                str(props.get("OBJECTID") or props.get("objectid") or "") or None,
                props.get("nomor"),
                props.get("persilpasifid"),
                props.get("tipehak") or props.get("TIPEHAK"),
                props.get("penggunaan") or props.get("PENGGUNAAN"),
                props.get("luas") or props.get("LUAS"),
                props.get("akurasibidang") or props.get("AKURASIBIDANG"),
                props.get("alatukur"),
                props.get("tahun"),
                props.get("nilai"),
                postgres.source_layer,
                _to_bigint_or_none(props.get("kecamatan_id")),
                props.get("kecamatan_nama"),
                _to_bigint_or_none(props.get("kelurahan_id")),
                props.get("kelurahan_nama"),
                Json(props),
                wkt,
                wkt,
            )
        )

    try:
        with conn:
            with conn.cursor() as cur:
                insert_sql = f"""
                    INSERT INTO {postgres.table} (
                        source_key,
                        geometry_hash,
                        nib,
                        objectid,
                        nomor,
                        persilpasifid,
                        tipehak,
                        penggunaan,
                        luas,
                        akurasibidang,
                        alatukur,
                        tahun,
                        nilai,
                        source_layer,
                        kecamatan_id,
                        kecamatan_nama,
                        kelurahan_id,
                        kelurahan_nama,
                        raw_properties,
                        wkb_geometry
                    ) VALUES %s
                    ON CONFLICT DO NOTHING
                """
                row_template = """
                    (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE
                            WHEN %s IS NULL THEN NULL
                            ELSE ST_Multi(ST_GeomFromText(%s, 4326))
                        END
                    )
                """
                for start_index in range(0, len(rows), postgres.batch_size):
                    batch_rows = rows[start_index:start_index + postgres.batch_size]
                    execute_values(
                        cur,
                        insert_sql,
                        batch_rows,
                        template=row_template,
                        page_size=postgres.batch_size,
                    )
                    inserted += cur.rowcount
    finally:
        conn.close()

    return inserted
