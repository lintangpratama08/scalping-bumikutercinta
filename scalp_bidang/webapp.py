from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from .areas import get_area_geojson, list_areas
from .config import HttpConfig, PostgresConfig
from .engine import run_scrape


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = payload
        return job_id

    def update(self, job_id: str, patch: dict[str, Any]) -> None:
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(patch)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._jobs.get(job_id)
            return deepcopy(payload) if payload else None


def create_app() -> Flask:
    project_root = Path(__file__).resolve().parent.parent
    output_dir = project_root / "output"
    app = Flask(__name__, static_folder=str(project_root), static_url_path="")
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scalp-bidang")
    jobs = JobStore()

    @app.get("/")
    def index():
        return send_from_directory(project_root, "monitor.html")

    @app.get("/api/areas")
    def api_areas():
        level = (request.args.get("level") or "kecamatan").strip().lower()
        kecamatan_id = request.args.get("kecamatan_id", type=int)
        items = list_areas(level, PostgresConfig(), kecamatan_id=kecamatan_id)
        return jsonify({"items": items})

    @app.get("/api/area-geojson")
    def api_area_geojson():
        level = (request.args.get("level") or "kecamatan").strip().lower()
        area_id = request.args.get("area_id", type=int)
        if not area_id:
            return jsonify({"error": "area_id wajib diisi"}), 400
        payload = get_area_geojson(level, area_id, PostgresConfig())
        if not payload:
            return jsonify({"error": "area tidak ditemukan"}), 404
        return jsonify(payload)

    @app.get("/api/stored-parcels")
    def api_stored_parcels():
        level = (request.args.get("level") or "kecamatan").strip().lower()
        area_id = request.args.get("area_id", type=int)
        limit = min(max(request.args.get("limit", default=3000, type=int), 1), 10000)
        if level not in {"kecamatan", "kelurahan"}:
            return jsonify({"error": "level harus kecamatan atau kelurahan"}), 400
        if not area_id:
            return jsonify({"error": "area_id wajib diisi"}), 400

        import psycopg2

        postgres = PostgresConfig()
        conn = psycopg2.connect(
            host=postgres.host,
            port=postgres.port,
            user=postgres.user,
            password=postgres.password,
            dbname=postgres.dbname,
        )
        try:
            with conn.cursor() as cur:
                id_field = "kecamatan_id" if level == "kecamatan" else "kelurahan_id"
                cur.execute(
                    f"""
                    SELECT
                        id,
                        source_key,
                        geometry_hash,
                        nib,
                        objectid,
                        persilpasifid,
                        nomor,
                        ST_AsGeoJSON(wkb_geometry)::json
                    FROM {postgres.table}
                    WHERE {id_field} = %s
                      AND wkb_geometry IS NOT NULL
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    [area_id, limit],
                )
                features = []
                for row in cur.fetchall():
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": row[7],
                            "properties": {
                                "id": row[0],
                                "source_key": row[1],
                                "geometry_hash": row[2],
                                "nib": row[3],
                                "objectid": row[4],
                                "persilpasifid": row[5],
                                "nomor": row[6],
                            },
                        }
                    )
                return jsonify({"type": "FeatureCollection", "features": features})
        finally:
            conn.close()

    @app.get("/api/latest-run")
    def api_latest_run():
        latest_path = output_dir / "latest_run.json"
        if not latest_path.exists():
            return jsonify({"error": "latest_run.json belum ada"}), 404
        return send_from_directory(output_dir, "latest_run.json")

    @app.get("/api/jobs/<job_id>")
    def api_job_status(job_id: str):
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "job tidak ditemukan"}), 404
        return jsonify(job)

    @app.get("/output/<path:filename>")
    def serve_output(filename: str):
        return send_from_directory(output_dir, filename)

    @app.post("/api/scrape")
    def api_scrape():
        payload = request.get_json(silent=True) or {}
        level = str(payload.get("level") or "kecamatan").strip().lower()
        area_id = int(payload.get("area_id") or 0)
        if level not in {"kecamatan", "kelurahan"}:
            return jsonify({"error": "level harus kecamatan atau kelurahan"}), 400
        if area_id <= 0:
            return jsonify({"error": "area_id wajib diisi"}), 400

        limit = int(payload.get("limit") or 0)
        export_files = bool(payload.get("export_files", True))
        no_postgres = bool(payload.get("no_postgres", False))
        coverage = str(payload.get("coverage") or "aggressive").strip().lower()
        output_name = str(payload.get("output_name") or f"ui_{level}_{area_id}").strip()
        concurrency = int(payload.get("concurrency") or 12)
        timeout = float(payload.get("timeout") or 30.0)

        job_id = jobs.create(
            {
                "job_id": "",
                "status": "queued",
                "message": "Job masuk antrian",
                "areas": 0,
                "features_total": 0,
                "inserted_total": 0,
                "coverage": coverage,
                "concurrency": concurrency,
                "results": [],
            }
        )
        jobs.update(job_id, {"job_id": job_id})

        def run_job() -> None:
            try:
                jobs.update(job_id, {"status": "running", "message": "Scraping sedang berjalan"})
                result = run_scrape(
                    polygon_path=None,
                    polygon_db_source=level,
                    polygon_name_field="nama",
                    selected_names=None,
                    selected_ids=[area_id],
                    coverage=coverage,
                    limit=limit,
                    limit_per_area=0,
                    export_files=export_files,
                    output_dir=output_dir,
                    output_name=output_name,
                    postgres_enabled=not no_postgres,
                    skip_existing=True,
                    postgres=PostgresConfig(),
                    http_config=HttpConfig(
                        timeout_seconds=timeout,
                        concurrency=concurrency,
                        connect_limit=max(concurrency * 4, 32),
                        keepalive_limit=max(concurrency * 2, 16),
                    ),
                    progress_callback=lambda progress: jobs.update(
                        job_id,
                        {
                            **progress,
                            "job_id": job_id,
                            "message": f"Area aktif: {progress.get('current_area_name') or '-'}",
                        },
                    ),
                )
                jobs.update(job_id, {"status": "completed", "message": "Scraping selesai", **result})
            except Exception as exc:
                jobs.update(job_id, {"status": "failed", "message": str(exc)})

        executor.submit(run_job)
        return jsonify({"job_id": job_id, "status": "queued"})

    return app
