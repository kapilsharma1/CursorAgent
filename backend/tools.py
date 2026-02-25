"""
Agent tools: search_symbol, get_file, apply_patch, web_search. LLM never edits files directly.
"""

import logging
from typing import Any

from config import get_settings
from diff_utils import apply_patch as apply_patch_impl, validate_diff
from retrieval import get_query_embedding, retrieve
from workspace_utils import get_repo_root, read_file_content, resolve_file_path

logger = logging.getLogger(__name__)


def web_search(query: str) -> list[dict[str, Any]]:
    """
    Search the web using Tavily API. Returns a list of {title, url, content} dicts.
    Returns empty list or a single error-message item if API key is missing or request fails.
    """
    logger.debug("web_search query=%s", query[:100] if query else "")
    if not (query or "").strip():
        return []
    settings = get_settings()
    if not settings.tavily_api_key:
        logger.debug("web_search skipped (no Tavily API key)")
        return [{"title": "Configuration", "url": "", "content": "Tavily API key not configured. Set TAVILY_API_KEY to enable web search."}]
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=settings.tavily_api_key)
        response = client.search((query or "").strip(), max_results=8)
        results = response.get("results") or []
        out = []
        for r in results:
            out.append({
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "content": (r.get("content") or "")[:1000],
            })
        logger.debug("web_search query=%s results=%s", query[:50], len(out))
        return out
    except Exception as e:
        logger.exception("web_search failed query=%s: %s", query[:50], e)
        return [{"title": "Error", "url": "", "content": f"Web search failed: {e!s}"}]


def search_symbol(name: str, session_id: str) -> list[dict[str, Any]]:
    """
    Query Pinecone (session namespace) by symbol name via metadata filter or semantic search.
    Return matching chunks with file and line info.
    """
    logger.debug("search_symbol name=%s session_id=%s", name, session_id)
    if not name or not session_id:
        logger.debug("search_symbol skipped (empty name or session_id)")
        return []
    settings = get_settings()
    if not settings.pinecone_api_key:
        logger.debug("search_symbol skipped (no Pinecone API key)")
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
        result = chunks[:5]
        logger.debug("search_symbol name=%s session_id=%s found=%s", name, session_id, len(result))
        return result
    except Exception as e:
        logger.exception("search_symbol failed name=%s session_id=%s: %s", name, session_id, e)
        return []


def get_file(path: str, session_id: str) -> str | None:
    """
    Resolve path under workspace/{session_id}/repo, validate no traversal and max size.
    Return file content (text only) or None.
    """
    logger.debug("get_file path=%s session_id=%s", path, session_id)
    resolved = resolve_file_path(session_id, path)
    if not resolved:
        logger.debug("get_file unresolved path=%s session_id=%s", path, session_id)
        return None
    content = read_file_content(resolved)
    logger.debug("get_file path=%s session_id=%s len=%s", path, session_id, len(content) if content else 0)
    return content


def apply_patch(diff: str, session_id: str) -> dict[str, Any]:
    """
    Validate and apply patch. Returns { "success": bool, "updated_files": {...} or "error": str }.
    """
    logger.debug("apply_patch session_id=%s diff_len=%s", session_id, len(diff))
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        logger.warning("apply_patch repo not found session_id=%s", session_id)
        return {"success": False, "error": "Repo not found for session."}
    ok, err = validate_diff(diff, repo_root)
    if not ok:
        logger.warning("apply_patch validation failed session_id=%s: %s", session_id, err)
        return {"success": False, "error": err}
    result = apply_patch_impl(diff, repo_root)
    if isinstance(result, str):
        logger.error("apply_patch apply failed session_id=%s: %s", session_id, result)
        return {"success": False, "error": result}
    logger.info("apply_patch success session_id=%s files=%s", session_id, list(result.keys()))
    return {"success": True, "updated_files": result}
