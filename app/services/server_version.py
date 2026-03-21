from functools import lru_cache
from pathlib import Path
import subprocess

from app.config import get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVER_VERSION = "ver1.0.5"


def _run_git_command(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    return result.stdout.strip()


@lru_cache
def get_server_version() -> str:
    configured_version = get_settings().app_version.strip()
    if configured_version:
        return configured_version

    try:
        revision = _run_git_command("rev-parse", "--short=12", "HEAD")
        if not revision:
            return DEFAULT_SERVER_VERSION
        dirty = bool(_run_git_command("status", "--porcelain"))
        return f"{revision}-dirty" if dirty else revision
    except Exception:
        return DEFAULT_SERVER_VERSION
