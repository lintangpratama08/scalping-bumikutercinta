from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(os.getenv("SCALP_ENV_FILE", Path(__file__).resolve().parents[1] / ".env"))
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()

        key, separator, value = line.partition("=")
        if not separator:
            continue

        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _env(*names: str, default: str) -> str:
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


_load_dotenv()


@dataclass(frozen=True)
class PostgresConfig:
    host: str = _env("SCALP_DB_HOST", "DB_HOST", default="127.0.0.1")
    port: int = int(_env("SCALP_DB_PORT", "DB_PORT", default="5432"))
    user: str = _env("SCALP_DB_USER", "DB_USERNAME", "DB_USER", default="postgres")
    password: str = _env("SCALP_DB_PASSWORD", "DB_PASSWORD", default="root")
    dbname: str = _env("SCALP_DB_NAME", "DB_DATABASE", "DB_NAME", default="map_kab_bandung")
    table: str = _env("SCALP_DB_TABLE", default="data.data_bhumi_bidang_tanah")
    source_layer: str = _env("SCALP_SOURCE_LAYER", default="bhumi_persil_public")
    batch_size: int = int(_env("SCALP_DB_BATCH_SIZE", default="500"))


@dataclass(frozen=True)
class HttpConfig:
    timeout_seconds: float = float(_env("SCALP_HTTP_TIMEOUT", default="30"))
    concurrency: int = int(_env("SCALP_HTTP_CONCURRENCY", default="12"))
    connect_limit: int = int(_env("SCALP_HTTP_CONNECT_LIMIT", default="64"))
    keepalive_limit: int = int(_env("SCALP_HTTP_KEEPALIVE_LIMIT", default="32"))


@dataclass(frozen=True)
class BhumiLiveConfig:
    helper_php: str = _env("SCALP_BHUMI_HELPER_PHP", default=r"C:\laragon\www\JOKI\bhumi-capture-importer\run.php")
    helper_retries: int = int(_env("SCALP_BHUMI_HELPER_RETRIES", default="3"))
    helper_timeout_seconds: int = int(_env("SCALP_BHUMI_HELPER_TIMEOUT", default="600"))


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://bhumi.atrbpn.go.id/peta",
    "Accept": "application/json",
}
