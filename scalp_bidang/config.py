from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PostgresConfig:
    host: str = os.getenv("SCALP_DB_HOST", "127.0.0.1")
    port: int = int(os.getenv("SCALP_DB_PORT", "5432"))
    user: str = os.getenv("SCALP_DB_USER", "postgres")
    password: str = os.getenv("SCALP_DB_PASSWORD", "root")
    dbname: str = os.getenv("SCALP_DB_NAME", "map_kab_bandung")
    table: str = os.getenv("SCALP_DB_TABLE", "data.data_bhumi_bidang_tanah")
    source_layer: str = os.getenv("SCALP_SOURCE_LAYER", "bhumi_persil_public")
    batch_size: int = int(os.getenv("SCALP_DB_BATCH_SIZE", "500"))


@dataclass(frozen=True)
class HttpConfig:
    timeout_seconds: float = float(os.getenv("SCALP_HTTP_TIMEOUT", "30"))
    concurrency: int = int(os.getenv("SCALP_HTTP_CONCURRENCY", "12"))
    connect_limit: int = int(os.getenv("SCALP_HTTP_CONNECT_LIMIT", "64"))
    keepalive_limit: int = int(os.getenv("SCALP_HTTP_KEEPALIVE_LIMIT", "32"))


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://bhumi.atrbpn.go.id/peta",
    "Accept": "application/json",
}
