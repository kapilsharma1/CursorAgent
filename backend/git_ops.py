"""
Git operations for session repo: commit and push to origin.
Uses session meta (repo_url, session_branch); supports force push with --force-with-lease.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from config import get_settings
from workspace_utils import get_repo_root, load_session_meta

logger = logging.getLogger(__name__)

# Detect non-fast-forward push error from git
PUSH_REJECTED_RE = re.compile(
    r"!?\s*\[rejected\].*?(?:non-fast-forward|fetch first|Updates were rejected)",
    re.IGNORECASE | re.DOTALL,
)


def _push_url_with_token(repo_url: str, token: str) -> str:
    """Inject token into HTTPS URL for authenticated push. Leaves URL unchanged if token empty."""
    if not (token and repo_url.strip().startswith("https://")):
        return repo_url.strip()
    # https://github.com/owner/repo.git -> https://TOKEN@github.com/owner/repo.git
    url = repo_url.strip()
    if "@" in url.split("//")[-1]:
        return url  # already has credentials
    return url.replace("https://", f"https://{token}@", 1)


async def _run_git(repo_root: Path, *args: str) -> tuple[int, str, str]:
    """Run git command in repo_root; return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = (stdout or b"").decode("utf-8", errors="replace").strip()
    err = (stderr or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode, out, err


async def _remote_branch_exists(repo_root: Path, branch: str) -> bool:
    """Return True if branch exists on remote (ls-remote)."""
    code, out, err = await _run_git(repo_root, "ls-remote", "--heads", "origin", branch)
    if code != 0:
        logger.debug("_remote_branch_exists ls-remote branch=%s err=%s", branch, err[:200])
        return False
    return bool(out.strip())


async def commit_and_push(
    session_id: str,
    commit_message: str = "Updates from Cursor Clone",
    force: bool = False,
) -> dict[str, Any]:
    """
    Stage all changes, commit, and push to session branch.
    Uses session meta for repo_url and session_branch.
    Returns dict: success (bool), message (str), error (str | None), force_required (bool).
    """
    logger.info("commit_and_push start session_id=%s commit_message=%s force=%s", session_id, commit_message, force)
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        logger.warning("commit_and_push repo not found session_id=%s repo_root=%s", session_id, repo_root)
        return {"success": False, "message": "Repo not found.", "error": "Repo not found for session.", "force_required": False}

    meta = load_session_meta(session_id)
    if not meta:
        logger.warning("commit_and_push session meta not found session_id=%s", session_id)
        return {"success": False, "message": "Session meta not found.", "error": "No push target (clone may predate session branch).", "force_required": False}

    repo_url = meta.get("repo_url") or ""
    branch = meta.get("session_branch") or ""
    if not repo_url or not branch:
        logger.warning("commit_and_push invalid meta session_id=%s repo_url=%s branch=%s", session_id, bool(repo_url), branch or "(empty)")
        return {"success": False, "message": "Invalid session meta.", "error": "Missing repo_url or session_branch.", "force_required": False}

    settings = get_settings()
    token = (settings.github_token or "").strip()
    push_url = _push_url_with_token(repo_url, token)
    logger.info("commit_and_push repo_root=%s branch=%s repo_url_set=%s token_set=%s", repo_root, branch, bool(repo_url), bool(token))

    # Ensure remote origin uses push URL (for auth)
    code, _, err = await _run_git(repo_root, "remote", "get-url", "origin")
    if code == 0 and push_url != repo_url:
        await _run_git(repo_root, "remote", "set-url", "origin", push_url)

    # Config user for commit
    await _run_git(repo_root, "config", "user.name", settings.github_commit_username)
    await _run_git(repo_root, "config", "user.email", settings.github_commit_email)

    # Stage all
    code, out, err = await _run_git(repo_root, "add", "-A")
    if code != 0:
        logger.warning("commit_and_push git add failed session_id=%s code=%s err=%s", session_id, code, (err or out)[:300])
        return {"success": False, "message": "Git add failed.", "error": err or out, "force_required": False}
    logger.info("commit_and_push git add ok session_id=%s", session_id)

    # Status to see if there are changes
    code, status_out, _ = await _run_git(repo_root, "status", "--porcelain")
    if code == 0 and not status_out.strip():
        logger.info("commit_and_push nothing to commit session_id=%s (no changes after add -A)", session_id)
        return {"success": True, "message": "Nothing to commit.", "error": None, "force_required": False}
    logger.info("commit_and_push has changes session_id=%s status_lines=%s", session_id, len(status_out.strip().splitlines()))

    # Commit
    safe_msg = (commit_message or "Updates from Cursor Clone").replace('"', '\\"')[:500]
    code, out, err = await _run_git(repo_root, "commit", "-m", safe_msg)
    if code != 0:
        logger.warning("commit_and_push commit failed session_id=%s code=%s err=%s", session_id, code, (err or out)[:300])
        return {"success": False, "message": "Commit failed.", "error": err or out, "force_required": False}
    logger.info("commit_and_push commit ok session_id=%s", session_id)

    # Push: first time use -u, later just push. Use force-with-lease if force=True.
    push_args = ["push", "origin", branch]
    if force:
        push_args.insert(2, "--force-with-lease")
    else:
        remote_exists = await _remote_branch_exists(repo_root, branch)
        logger.info("commit_and_push remote branch exists session_id=%s branch=%s remote_exists=%s", session_id, branch, remote_exists)
        if not remote_exists:
            push_args.insert(2, "-u")
    logger.info("commit_and_push push args session_id=%s args=%s", session_id, push_args)

    code, out, err = await _run_git(repo_root, *push_args)
    if code == 0:
        logger.info("commit_and_push push ok session_id=%s branch=%s out=%s", session_id, branch, (out or "")[:200])
        return {"success": True, "message": "Pushed successfully.", "error": None, "force_required": False}

    combined = (err + "\n" + out).strip()
    force_required = bool(PUSH_REJECTED_RE.search(combined))
    logger.warning(
        "commit_and_push push failed session_id=%s branch=%s code=%s force_required=%s err=%s",
        session_id, branch, code, force_required, combined[:500],
    )
    return {
        "success": False,
        "message": "Push failed." + (" Use force push to overwrite remote." if force_required else ""),
        "error": combined[:1000],
        "force_required": force_required,
    }