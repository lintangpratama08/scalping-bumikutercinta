from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def _candidate_pythons() -> list[Path]:
    base_dir = Path(__file__).resolve().parent
    candidates = []
    if os.name == "nt":
        candidates.append(base_dir / "venv" / "Scripts" / "python.exe")
        candidates.append(base_dir.parent / "api-smartmap" / "venv" / "Scripts" / "python.exe")
    else:
        candidates.append(base_dir / "venv" / "bin" / "python")
        candidates.append(base_dir.parent / "api-smartmap" / "venv" / "bin" / "python")
    return [path for path in candidates if path.exists()]


def _module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _reexec_with_available_venv() -> int:
    for python_path in _candidate_pythons():
        completed = subprocess.run([str(python_path), str(Path(__file__).resolve()), *sys.argv[1:]], check=False)
        return completed.returncode
    return 1


def _bootstrap() -> int | None:
    if _module_exists("httpx") and _module_exists("psycopg2") and _module_exists("flask"):
        return None
    return _reexec_with_available_venv()


if __name__ == "__main__":
    bootstrap_result = _bootstrap()
    if bootstrap_result is not None:
        raise SystemExit(bootstrap_result)

    from scalp_bidang.cli import main

    raise SystemExit(main())
