"""
Unified diff: parse, validate (path traversal, file count, line count, blocklist), apply.
"""

import re
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Blocklisted paths that must not be modified
BLOCKLIST = {".env", ".env.local", ".env.production", "package-lock.json", "yarn.lock"}


def parse_unified_diff(diff: str) -> list[dict[str, Any]]:
    """
    Parse unified diff into list of { file_path, hunks }.
    file_path is the path after --- or +++ (we use b-side, the target file).
    Reject if format is invalid (no valid @@ or invalid line prefixes).
    """
    if not diff or not diff.strip():
        return []
    result: list[dict[str, Any]] = []
    current_file: str | None = None
    current_hunks: list[str] = []
    for line in diff.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            if current_file is not None and current_hunks:
                result.append({"file_path": current_file, "hunks": current_hunks})
            current_hunks = []
            if line.startswith("+++ "):
                # Target file (after +++); strip a/b prefix
                p = line[4:].strip()
                if p.startswith("a/") or p.startswith("b/"):
                    p = p[2:]
                current_file = p
            continue
        if line.startswith("@@ "):
            current_hunks.append(line)
            continue
        if current_file is not None and current_hunks and (
            line.startswith("+") or line.startswith("-") or line.startswith(" ")
        ):
            current_hunks.append(line)
    if current_file is not None and current_hunks:
        result.append({"file_path": current_file, "hunks": current_hunks})
    return result


def validate_diff(diff: str, repo_root: Path) -> tuple[bool, str]:
    """
    Deterministic validation: format, path safety, file count, line count.
    Returns (ok, error_message).
    """
    from config import get_settings
    logger.debug("validate_diff repo_root=%s diff_len=%s", repo_root, len(diff or ""))
    settings = get_settings()
    max_files = settings.max_files_in_patch
    max_lines = settings.max_patch_lines
    if not diff or not diff.strip():
        logger.debug("validate_diff rejected: empty diff")
        return False, "Empty diff"
    parsed = parse_unified_diff(diff)
    if not parsed:
        logger.debug("validate_diff rejected: invalid format")
        return False, "Invalid unified diff format"
    if len(parsed) > max_files:
        return False, f"Too many files (max {max_files})"
    total_lines = 0
    repo_root = repo_root.resolve()
    for item in parsed:
        file_path = item.get("file_path") or ""
        if ".." in file_path or file_path.startswith("/") or "\\" in file_path and ".." in file_path:
            return False, "Path traversal not allowed"
        # Normalize
        clean = file_path.strip().lstrip("/").replace("\\", "/")
        if not clean:
            return False, "Empty file path"
        if any(clean.endswith(bl) or bl in clean.split("/") for bl in BLOCKLIST):
            return False, "Blocklisted path"
        resolved = (repo_root / clean).resolve()
        try:
            if not resolved.is_relative_to(repo_root):
                return False, "Path outside repo"
        except (ValueError, OSError):
            return False, "Invalid path"
        if not resolved.exists():
            # New file is ok for patch
            pass
        for hunk in item.get("hunks") or []:
            if hunk.startswith("+") or hunk.startswith("-"):
                total_lines += 1
    if total_lines > max_lines:
        logger.debug("validate_diff rejected: too many lines total=%s max=%s", total_lines, max_lines)
        return False, f"Too many lines changed (max {max_lines})"
    logger.debug("validate_diff ok files=%s", len(parsed))
    return True, ""


def _apply_hunk_to_lines(lines: list[str], hunk: Any) -> list[str]:
    """Apply a single hunk (unidiff Hunk) to lines; return new lines."""
    # unidiff: source_start is 1-based, source_length is number of source lines
    start = getattr(hunk, "source_start", 1) - 1
    start = max(0, start)
    source_len = getattr(hunk, "source_length", 0)
    # Short form @@ -1 +1 @@ can leave source_length 0; treat as 1 line so we replace instead of insert
    if source_len <= 0:
        source_len = 1
    end = min(start + source_len, len(lines))
    # Result of hunk: context and added lines (no removed)
    new_block: list[str] = []
    for line in hunk:
        val = getattr(line, "value", "")
        if not val.endswith("\n"):
            val = val + "\n"
        if getattr(line, "is_removed", False):
            continue
        new_block.append(val)
    return lines[:start] + new_block + lines[end:]


def apply_patch(diff: str, repo_root: Path) -> dict[str, str] | str:
    """
    Apply unified diff to repo. Must call validate_diff first.
    Returns dict path -> new_content for changed files, or error message string.
    """
    logger.debug("apply_patch start repo_root=%s", repo_root)
    ok, err = validate_diff(diff, repo_root)
    if not ok:
        return err
    try:
        from unidiff import PatchSet
        from io import StringIO
    except ImportError:
        logger.error("apply_patch unidiff not installed")
        return "unidiff not installed"
    try:
        patch = PatchSet(StringIO(diff))
    except Exception as e:
        logger.warning("apply_patch parse failed: %s", e)
        return f"Invalid diff: {e}"
    repo_root = repo_root.resolve()
    updated: dict[str, str] = {}
    for patched_file in patch:
        path = getattr(patched_file, "target_file", None) or getattr(patched_file, "path", "") or ""
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        full = (repo_root / path).resolve()
        if not full.is_relative_to(repo_root):
            continue
        try:
            current = full.read_text(encoding="utf-8", errors="replace") if full.exists() else ""
        except Exception as e:
            return f"Could not read {path}: {e}"
        lines = current.splitlines(keepends=True)
        if not lines and current:
            lines = [current]
        elif not lines:
            lines = [""]
        for hunk in patched_file:
            lines = _apply_hunk_to_lines(lines, hunk)
        new_content = "".join(lines)
        updated[path] = new_content
    for path, content in updated.items():
        full = repo_root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    logger.info("apply_patch success repo_root=%s updated=%s", repo_root, list(updated.keys()))
    return updated
