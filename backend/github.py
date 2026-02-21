"""
Clone public GitHub repos into session workspace; validate URL and size.
"""

import asyncio
import re
import shutil
import logging
from pathlib import Path

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Match https://github.com/owner/repo or git@github.com:owner/repo.git
GITHUB_URL_PATTERN = re.compile(
    r"^(https://github\.com/[\w.-]+/[\w.-]+?)(?:\.git)?/?$|^git@github\.com:([\w.-]+/[\w.-]+?)(?:\.git)?$",
    re.IGNORECASE,
)


def is_github_public_url(repo_url: str) -> bool:
    """Return True if URL looks like a public GitHub repo (no OAuth)."""
    return bool(GITHUB_URL_PATTERN.match(repo_url.strip()))


def normalize_github_url(repo_url: str) -> str:
    """Return https clone URL for the repo."""
    repo_url = repo_url.strip()
    m = GITHUB_URL_PATTERN.match(repo_url)
    if not m:
        raise ValueError("Invalid GitHub repo URL")
    if m.group(1):
        return m.group(1).rstrip("/") + ".git"
    return f"https://github.com/{m.group(2)}.git"


async def clone_repo(repo_url: str, dest_path: Path) -> None:
    """
    Clone a public GitHub repo into dest_path.
    - Validates GitHub URL
    - Enforces max repo size (10MB) after clone
    - Uses shallow clone (depth 1)
    """
    if not is_github_public_url(repo_url):
        raise ValueError("Only public GitHub repo URLs are supported")

    settings = get_settings()
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if dest_path.exists():
        shutil.rmtree(dest_path)

    url = normalize_github_url(repo_url)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        url,
        str(dest_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode().strip()
        raise RuntimeError(f"git clone failed: {err}")

    # Enforce total repo size cap
    total = 0
    for f in dest_path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
            if total > settings.max_repo_bytes:
                shutil.rmtree(dest_path, ignore_errors=True)
                raise ValueError(
                    f"Repo exceeds max size ({settings.max_repo_bytes} bytes). Aborted."
                )
    logger.info("Cloned repo into %s (total bytes %s)", dest_path, total)
