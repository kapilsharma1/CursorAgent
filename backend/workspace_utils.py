"""
Workspace helpers: build file tree, resolve safe file paths, read file content.
"""

import logging
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


def get_repo_root(session_id: str) -> Path:
    """Path to the cloned repo for this session."""
    return get_settings().repo_path(session_id)


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
