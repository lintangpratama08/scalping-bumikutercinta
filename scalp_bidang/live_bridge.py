from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .areas import load_kelurahan_areas_by_kecamatan_ids
from .config import BhumiLiveConfig, PostgresConfig
from .importer import import_features_file
from .models import PolygonArea


def _resolve_kelurahan_targets(
    areas: list[PolygonArea],
    polygon_db_source: str | None,
    postgres: PostgresConfig,
) -> list[PolygonArea]:
    source = (polygon_db_source or "").strip().lower()
    if source == "kelurahan":
        return [area for area in areas if area.id is not None]
    if source == "kecamatan":
        kecamatan_ids = [int(area.id) for area in areas if area.id is not None]
        return load_kelurahan_areas_by_kecamatan_ids(kecamatan_ids, postgres)
    return []


def _extract_output_path(stdout: str) -> Path | None:
    match = re.search(r"File output:\s*(.+)", stdout)
    if not match:
        return None
    return Path(match.group(1).strip())


def _read_feature_count(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features") if isinstance(payload, dict) else None
    return len(features) if isinstance(features, list) else 0


def export_kelurahan_geojson_via_helper(
    kelurahan_id: int,
    config: BhumiLiveConfig,
) -> tuple[Path, str]:
    helper_path = Path(config.helper_php)
    if not helper_path.is_file():
        raise FileNotFoundError(f"Helper PHP tidak ditemukan: {helper_path}")

    command = ["php", str(helper_path), "kelurahan:geojson", f"--id={kelurahan_id}"]
    last_error = ""

    for attempt in range(1, max(config.helper_retries, 1) + 1):
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.helper_timeout_seconds,
            check=False,
            cwd=str(helper_path.parent),
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        combined = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part.strip())

        if completed.returncode == 0:
            output_path = _extract_output_path(stdout)
            if output_path is None or not output_path.is_file():
                raise RuntimeError(f"Helper selesai tanpa file output yang valid. Output:\n{combined}")
            return output_path, combined

        last_error = combined or f"helper exit code {completed.returncode}"

    raise RuntimeError(f"Gagal export live BHUMI untuk kelurahan {kelurahan_id}: {last_error}")


def run_live_geojson_pipeline(
    areas: list[PolygonArea],
    polygon_db_source: str | None,
    postgres: PostgresConfig,
    postgres_enabled: bool,
    output_dir: Path,
    export_files: bool,
    progress_callback: Any = None,
) -> dict[str, Any]:
    live_config = BhumiLiveConfig()
    targets = _resolve_kelurahan_targets(areas, polygon_db_source, postgres)
    if not targets:
        raise ValueError("Mode live BHUMI saat ini mendukung polygon_db_source kelurahan atau kecamatan dengan ID area yang valid.")

    results: list[dict[str, Any]] = []
    features_total = 0
    inserted_total = 0

    for index, area in enumerate(targets, start=1):
        area_result: dict[str, Any] = {
            "area_id": area.id,
            "area_level": "kelurahan",
            "area": area.name,
            "features": 0,
            "inserted": 0,
            "duplicates": 0,
            "errors": [],
            "outputs": {},
            "status": "running",
        }
        results.append(area_result)

        try:
            output_path, helper_log = export_kelurahan_geojson_via_helper(int(area.id), live_config)
            feature_count = _read_feature_count(output_path)
            area_result["features"] = feature_count
            features_total += feature_count

            if export_files:
                output_dir.mkdir(parents=True, exist_ok=True)
                copied_path = output_dir / output_path.name
                shutil.copyfile(output_path, copied_path)
                area_result["outputs"]["geojson"] = str(copied_path)
                import_path = copied_path
            else:
                import_path = output_path

            if postgres_enabled:
                import_result = import_features_file(
                    import_path,
                    postgres,
                    polygon_db_source="kelurahan",
                    selected_ids=[int(area.id)],
                )
                inserted = int(import_result.get("inserted") or 0)
                duplicates = int(import_result.get("duplicates_or_conflicts") or 0) + int(import_result.get("filtered_out") or 0)
                inserted_total += inserted
                area_result["inserted"] = inserted
                area_result["duplicates"] = max(feature_count - inserted, duplicates)
            area_result["status"] = "completed"
            area_result["helper_log"] = helper_log
        except Exception as exc:
            area_result["status"] = "failed"
            area_result["errors"] = [str(exc)]

        if progress_callback:
            progress_callback(
                {
                    "areas": len(targets),
                    "features_total": features_total,
                    "inserted_total": inserted_total,
                    "coverage": "bhumi-live",
                    "results": results,
                    "current_area_id": area.id,
                    "current_area_name": area.name,
                    "status": "running" if index < len(targets) else "completed",
                }
            )

    return {
        "areas": len(targets),
        "features_total": features_total,
        "inserted_total": inserted_total,
        "coverage": "bhumi-live",
        "results": results,
        "status": "completed",
    }
