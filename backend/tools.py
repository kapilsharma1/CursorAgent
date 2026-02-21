"""
Agent tools: search_symbol, get_file, apply_patch. LLM never edits files directly.
"""

import logging
from typing import Any

from backend.config import get_settings
from backend.diff_utils import apply_patch as apply_patch_impl, validate_diff
from backend.retrieval import get_query_embedding, retrieve
from backend.workspace_utils import get_repo_root, read_file_content, resolve_file_path

logger = logging.getLogger(__name__)


def search_symbol(name: str, session_id: str) -> list[dict[str, Any]]:
    """
    Query Pinecone (session namespace) by symbol name via metadata filter or semantic search.
    Return matching chunks with file and line info.
    """
    if not name or not session_id:
        return []
    settings = get_settings()
    if not settings.pinecone_api_key:
        return []
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index_name)
        # Metadata filter: symbols contain the name (we stored as pipe-separated)
        # Pinecone serverless supports filter like {"symbols": {"$regex": "name"}} in some backends
        # Simpler: semantic search with query "symbol name <name>"
        query_embedding = get_query_embedding(f"symbol function class {name}")
        result = index.query(
            vector=query_embedding,
            top_k=10,
            namespace=session_id,
            include_metadata=True,
        )
        chunks = []
        for match in result.get("matches") or []:
            meta = match.get("metadata") or {}
            symbols_str = meta.get("symbols") or ""
            if name.lower() not in symbols_str.lower():
                continue
            chunks.append({
                "file": meta.get("file", ""),
                "symbols": (symbols_str.split("|") if symbols_str else []),
                "line": meta.get("start_line", 0),
                "content": (meta.get("content") or "")[:500],
            })
        return chunks[:5]
    except Exception as e:
        logger.exception("search_symbol failed: %s", e)
        return []


def get_file(path: str, session_id: str) -> str | None:
    """
    Resolve path under workspace/{session_id}/repo, validate no traversal and max size.
    Return file content (text only) or None.
    """
    resolved = resolve_file_path(session_id, path)
    if not resolved:
        return None
    return read_file_content(resolved)


def apply_patch(diff: str, session_id: str) -> dict[str, Any]:
    """
    Validate and apply patch. Returns { "success": bool, "updated_files": {...} or "error": str }.
    """
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        return {"success": False, "error": "Repo not found for session."}
    ok, err = validate_diff(diff, repo_root)
    if not ok:
        return {"success": False, "error": err}
    result = apply_patch_impl(diff, repo_root)
    if isinstance(result, str):
        return {"success": False, "error": result}
    return {"success": True, "updated_files": result}
