"""
Workspace helpers: build file tree, resolve safe file paths, read file content, session meta.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

# Valid git branch name: alphanumeric, -, _; no .. or leading dot.
BRANCH_NAME_RE = re.compile(r"^[a-zA-Z0-9/_.-]+$")


def get_repo_root(session_id: str) -> Path:
    """Path to the cloned repo for this session."""
    return get_settings().repo_path(session_id)


def session_meta_path(session_id: str) -> Path:
    """Path to session metadata file (repo_url, session_branch)."""
    return get_settings().workspace_root / session_id / "meta.json"


def session_branch_name(session_id: str) -> str:
    """Stable branch name for this session (valid git ref)."""
    short = (session_id or "").replace("-", "")[:8]
    if not short:
        short = "default"
    name = f"cursor-session-{short}"
    if not BRANCH_NAME_RE.match(name):
        name = "cursor-session-default"
    return name


def save_session_meta(session_id: str, repo_url: str, session_branch: str) -> None:
    """Persist repo_url and session_branch for this session."""
    path = session_meta_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"repo_url": repo_url, "session_branch": session_branch}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.debug("save_session_meta session_id=%s branch=%s", session_id, session_branch)


def load_session_meta(session_id: str) -> dict[str, Any] | None:
    """Load session meta (repo_url, session_branch). Returns None if missing or invalid."""
    path = session_meta_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("repo_url") and data.get("session_branch"):
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("load_session_meta failed session_id=%s: %s", session_id, e)
    return None


def is_safe_path(resolved: Path, repo_root: Path) -> bool:
    """True if resolved is under repo_root and no path traversal."""
    try:
        resolved = resolved.resolve()
        repo_root = repo_root.resolve()
        return resolved.is_relative_to(repo_root) and ".." not in resolved.relative_to(repo_root).parts
    except (ValueError, OSError):
        return False


def resolve_file_path(session_id: str, path: str) -> Path | None:
    """
    Resolve requested path to a file under session repo.
    Returns None if invalid or not under repo.
    """
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        logger.debug("resolve_file_path repo_root missing session_id=%s", session_id)
        return None
    # Normalize: no leading slash, no ..
    clean = path.strip().lstrip("/").replace("\\", "/")
    if ".." in clean or clean.startswith("/"):
        logger.debug("resolve_file_path rejected path=%s session_id=%s", path, session_id)
        return None
    resolved = (repo_root / clean).resolve()
    if not is_safe_path(resolved, repo_root):
        logger.debug("resolve_file_path unsafe path=%s session_id=%s", path, session_id)
        return None
    out = resolved if resolved.is_file() else None
    if not out:
        logger.debug("resolve_file_path not a file path=%s session_id=%s", path, session_id)
    return out


def build_file_tree(repo_root: Path) -> list[dict[str, Any]]:
    """
    Build a nested file tree for the repo.
    Excludes .git and other ignore_dirs.
    """
    settings = get_settings()
    ignore = set(settings.ignore_dirs)
    tree: list[dict[str, Any]] = []

    def add_node(parent_list: list, rel_path: Path, name: str, is_dir: bool) -> None:
        if is_dir and name in ignore:
            return
        node: dict[str, Any] = {"name": name, "path": str(rel_path)}
        if is_dir:
            node["children"] = []
            parent_list.append(node)
            return
        parent_list.append(node)

    def walk(current: Path, rel_path: Path, parent_list: list) -> None:
        if not current.exists():
            return
        try:
            entries = sorted(current.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if name in ignore:
                continue
            child_rel = rel_path / name if rel_path.parts else Path(name)
            if entry.is_dir():
                child_list: list[dict[str, Any]] = []
                add_node(parent_list, child_rel, name, True)
                # Find the node we just added to append children
                for n in parent_list:
                    if n.get("name") == name and "children" in n:
                        walk(entry, child_rel, n["children"])
                        break
            else:
                add_node(parent_list, child_rel, name, False)

    walk(repo_root, Path(""), tree)
    logger.debug("build_file_tree repo_root=%s entries=%s", repo_root, len(tree))
    return tree


def read_file_content(file_path: Path, max_bytes: int | None = None) -> str | None:
    """Read file as text; return None if binary or too large."""
    settings = get_settings()
    limit = max_bytes or settings.max_file_bytes
    try:
        size = file_path.stat().st_size
        if size > limit:
            logger.debug("read_file_content skipped (too large) path=%s size=%s limit=%s", file_path, size, limit)
            return None
        raw = file_path.read_bytes()
        # Simple binary check: null byte or high proportion of non-text
        if b"\x00" in raw:
            logger.debug("read_file_content skipped (binary) path=%s", file_path)
            return None
        return raw.decode("utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("read_file_content failed path=%s: %s", file_path, e)
        return None
