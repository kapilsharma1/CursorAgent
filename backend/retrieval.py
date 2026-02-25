"""
Retrieval: query Pinecone by session namespace; return chunks with file, symbols, language, content.
"""

import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


def retrieve(
    query_embedding: list[float],
    session_id: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    Query Pinecone in namespace=session_id; return list of chunks with
    file, symbols, language, content for context injection.
    """
    from openai import OpenAI
    from pinecone import Pinecone

    logger.info(
        "[retrieve] retrieve ENTRY session_id=%s namespace=%s top_k=%s embedding_len=%s",
        session_id, session_id, top_k, len(query_embedding) if query_embedding else 0,
    )
    settings = get_settings()
    if not settings.pinecone_api_key or not settings.openai_api_key:
        logger.warning("[retrieve] retrieve SKIP missing API keys")
        return []

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)
    namespace = session_id

    try:
        result = index.query(
            vector=query_embedding,
            top_k=top_k,
            namespace=namespace,
            include_metadata=True,
        )
    except Exception as e:
        logger.exception("[retrieve] Pinecone query FAILED session_id=%s namespace=%s: %s", session_id, namespace, e)
        return []

    matches = result.get("matches") or []
    chunks = []
    for match in matches:
        meta = match.get("metadata") or {}
        content = meta.get("content") or ""
        chunks.append({
            "file": meta.get("file", ""),
            "symbols": (meta.get("symbols") or "").split("|") if meta.get("symbols") else [],
            "language": meta.get("language", ""),
            "content": content,
            "start_line": meta.get("start_line", 0),
        })
    # Log first chunk content preview to verify if index is stale
    preview = ""
    if chunks:
        c = chunks[0]
        preview = (c.get("content") or "")[:120].replace("\n", " ")
    logger.info(
        "[retrieve] retrieve DONE session_id=%s namespace=%s matches=%s files=%s first_content_preview=%s",
        session_id, namespace, len(matches),
        [c.get("file") for c in chunks],
        repr(preview),
    )
    return chunks


def format_context_for_llm(chunks: list[dict[str, Any]]) -> str:
    """Format retrieved chunks for LLM context injection."""
    parts = []
    for c in chunks:
        file_path = c.get("file", "")
        symbols = c.get("symbols") or []
        content = c.get("content", "")
        parts.append(
            f"File: {file_path}\nSymbols: {', '.join(symbols)}\nCode:\n{content}"
        )
    return "\n\n---\n\n".join(parts) if parts else ""


def format_context_for_diff(chunks: list[dict[str, Any]]) -> str:
    """
    Format retrieved chunks with explicit line numbers so the coder can produce
    unified diffs that match the real file. Each line is prefixed with "  N| "
    where N is the 1-based line number (so @@ hunk headers use the correct numbers).
    """
    parts = []
    for c in chunks:
        file_path = c.get("file", "")
        symbols = c.get("symbols") or []
        content = c.get("content", "")
        start_line = int(c.get("start_line") or 1)
        line_list = content.splitlines(keepends=True)
        if not line_list and content:
            line_list = [content]
        numbered_lines = []
        for i, line in enumerate(line_list):
            line_no = start_line + i
            numbered_lines.append(f"  {line_no}| {line}" if line.endswith("\n") else f"  {line_no}| {line}\n")
        code_block = "".join(numbered_lines)
        parts.append(
            f"File: {file_path}\nSymbols: {', '.join(symbols)}\n"
            f"Code (line numbers must match your diff @@ hunk headers):\n{code_block}"
        )
    return "\n\n---\n\n".join(parts) if parts else ""


def get_query_embedding(query: str) -> list[float]:
    """Embed a query string using OpenAI-compatible API."""
    from openai import OpenAI

    logger.debug("get_query_embedding query_len=%s", len(query))
    settings = get_settings()
    openai_kwargs: dict = {"api_key": settings.openai_api_key}
    if settings.openai_base_url and settings.openai_base_url.strip():
        openai_kwargs["base_url"] = settings.openai_base_url.strip()
    client = OpenAI(**openai_kwargs)
    resp = client.embeddings.create(
        input=[query],
        model=settings.embedding_model,
    )
    return resp.data[0].embedding


def retrieve_and_format(query: str, session_id: str, top_k: int = 5) -> tuple[list[dict[str, Any]], str]:
    """Embed query, retrieve from Pinecone, return chunks and formatted context string."""
    logger.info("[retrieve] retrieve_and_format ENTRY session_id=%s top_k=%s query_len=%s", session_id, top_k, len(query))
    embedding = get_query_embedding(query)
    chunks = retrieve(embedding, session_id, top_k=top_k)
    context = format_context_for_llm(chunks)
    logger.info(
        "[retrieve] retrieve_and_format DONE session_id=%s chunks=%s context_len=%s context_preview=%s",
        session_id, len(chunks), len(context), repr(context[:200]) if context else "",
    )
    return chunks, context
