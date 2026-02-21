"""
Retrieval: query Pinecone by session namespace; return chunks with file, symbols, language, content.
"""

import logging
from typing import Any

from backend.config import get_settings

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

    settings = get_settings()
    if not settings.pinecone_api_key or not settings.openai_api_key:
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
        logger.exception("Pinecone query failed: %s", e)
        return []

    chunks = []
    for match in result.get("matches") or []:
        meta = match.get("metadata") or {}
        content = meta.get("content") or ""
        chunks.append({
            "file": meta.get("file", ""),
            "symbols": (meta.get("symbols") or "").split("|") if meta.get("symbols") else [],
            "language": meta.get("language", ""),
            "content": content,
            "start_line": meta.get("start_line", 0),
        })
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


def get_query_embedding(query: str) -> list[float]:
    """Embed a query string using OpenAI-compatible API."""
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    resp = client.embeddings.create(
        input=[query],
        model=settings.embedding_model,
    )
    return resp.data[0].embedding


def retrieve_and_format(query: str, session_id: str, top_k: int = 5) -> tuple[list[dict[str, Any]], str]:
    """Embed query, retrieve from Pinecone, return chunks and formatted context string."""
    embedding = get_query_embedding(query)
    chunks = retrieve(embedding, session_id, top_k=top_k)
    context = format_context_for_llm(chunks)
    return chunks, context
