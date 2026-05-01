from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import HttpConfig, PostgresConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scalp bidang BHUMI async tanpa UI web")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="Jalankan scraping bidang")
    scrape.add_argument("--polygon-path", default=None, help="Path GeoJSON polygon")
    scrape.add_argument("--polygon-db-source", default=None, help="Sumber polygon dari DB: kecamatan, kelurahan, atau nama tabel")
    scrape.add_argument("--polygon-name-field", default="nama", help="Field nama area")
    scrape.add_argument("--areas", default=None, help="Nama area dipisah koma")
    scrape.add_argument("--area-ids", default=None, help="ID area dipisah koma")
    scrape.add_argument(
        "--coverage",
        choices=["balanced", "aggressive", "saturation", "bhumi-full", "overpower"],
        default="overpower",
    )
    scrape.add_argument("--limit", type=int, default=0, help="Batas per area saat mode area tunggal")
    scrape.add_argument("--limit-per-area", type=int, default=0, help="Batas per area untuk batch")
    scrape.add_argument("--output-dir", default=str(Path("output")), help="Folder output")
    scrape.add_argument("--output-name", default="scalp_bidang", help="Prefix file output")
    scrape.add_argument("--export-files", action="store_true", help="Simpan GeoJSON/CSV/summary")
    scrape.add_argument("--no-postgres", action="store_true", help="Jangan insert ke PostgreSQL")
    scrape.add_argument("--no-skip-existing", action="store_true", help="Jangan cek geometry_hash existing")
    scrape.add_argument("--concurrency", type=int, default=12, help="Override concurrency minimum")
    scrape.add_argument("--timeout", type=float, default=30.0, help="Timeout HTTP per request")

    list_cmd = subparsers.add_parser("list-areas", help="Lihat area dari database")
    list_cmd.add_argument("--level", choices=["kecamatan", "kelurahan"], default="kecamatan")
    list_cmd.add_argument("--kecamatan-id", type=int, default=None)

    serve_cmd = subparsers.add_parser("serve", help="Jalankan dashboard HTML + API lokal")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=5055)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    postgres = PostgresConfig()

    if args.command == "list-areas":
        from .areas import list_areas

        print(json.dumps(list_areas(args.level, postgres, kecamatan_id=args.kecamatan_id), ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve":
        from .webapp import create_app

        app = create_app()
        app.run(host=args.host, port=args.port, debug=False)
        return 0

    selected_names = None
    if args.areas:
        selected_names = [item.strip() for item in args.areas.split(",") if item.strip()]
    selected_ids = None
    if args.area_ids:
        selected_ids = [int(item.strip()) for item in args.area_ids.split(",") if item.strip()]

    from .engine import run_scrape

    http_config = HttpConfig(
        timeout_seconds=args.timeout,
        concurrency=args.concurrency,
        connect_limit=max(args.concurrency * 4, 32),
        keepalive_limit=max(args.concurrency * 2, 16),
    )
    result = run_scrape(
        polygon_path=args.polygon_path,
        polygon_db_source=args.polygon_db_source,
        polygon_name_field=args.polygon_name_field,
        selected_names=selected_names,
        selected_ids=selected_ids,
        coverage=args.coverage,
        limit=args.limit,
        limit_per_area=args.limit_per_area,
        export_files=args.export_files,
        output_dir=Path(args.output_dir),
        output_name=args.output_name,
        postgres_enabled=not args.no_postgres,
        skip_existing=not args.no_skip_existing,
        postgres=postgres,
        http_config=http_config,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
