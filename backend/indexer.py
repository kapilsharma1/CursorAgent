"""
Indexing pipeline: walk repo, chunk by structure, embed, upsert to Pinecone (namespace=session_id).
"""

import logging
import re
import uuid
from pathlib import Path
from typing import Any

from config import get_settings
from workspace_utils import get_repo_root, read_file_content

logger = logging.getLogger(__name__)

# Extensions to index (code only)
# Language -> regex to find symbol boundaries (class, function, def, etc.)
SYMBOL_PATTERNS = {
    ".py": re.compile(r"^(\s*)(class |def |async def )\s+(\w+)", re.MULTILINE),
    ".js": re.compile(r"^(\s*)(class |function |async function |const \w+\s*=\s*(?:async\s+)?\(|export (?:function|class) )\s*(\w*)", re.MULTILINE),
    ".ts": re.compile(r"^(\s*)(class |function |async function |const \w+\s*=\s*(?:async\s+)?\(|export (?:function|class) )\s*(\w*)", re.MULTILINE),
    ".tsx": re.compile(r"^(\s*)(class |function |async function |const \w+\s*=\s*(?:async\s+)?\(|export (?:function|class) )\s*(\w*)", re.MULTILINE),
    ".java": re.compile(r"^(\s*)((?:public|private|protected)\s+)?(class |interface |enum |void \w+\s*\()\s*(\w*)", re.MULTILINE),
    ".go": re.compile(r"^(\s*)(func \w+|type \w+)", re.MULTILINE),
    ".c": re.compile(r"^(\s*)(\w+\s+\w+\s*\([^)]*\)\s*\{)", re.MULTILINE),
    ".cpp": re.compile(r"^(\s*)(class |struct |(?:void|int|bool|\w+)\s+\w+\s*\([^)]*\)\s*\{)", re.MULTILINE),
}


def _language_from_ext(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
        ".java": "java", ".go": "go", ".c": "c", ".cpp": "cpp",
    }.get(ext, "plain")


def walk_and_collect(session_id: str) -> list[tuple[Path, str]]:
    """
    Recursively walk repo; yield (file_path, content) for indexable files.
    Enforces total size cap and skips binary.
    """
    settings = get_settings()
    repo_root = get_repo_root(session_id)
    logger.debug("walk_and_collect session_id=%s repo_root=%s", session_id, repo_root)
    if not repo_root.exists():
        logger.warning("walk_and_collect repo_root does not exist session_id=%s", session_id)
        return []
    extensions = set(settings.index_extensions)
    ignore_dirs = set(settings.ignore_dirs)
    total_bytes = 0
    collected: list[tuple[Path, str]] = []

    for path in repo_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in extensions:
            if any(part in ignore_dirs for part in path.relative_to(repo_root).parts):
                continue
            if total_bytes + path.stat().st_size > settings.max_repo_bytes:
                logger.warning("Repo size cap reached; stopping index walk.")
                break
            content = read_file_content(path)
            if content is None:
                continue
            total_bytes += path.stat().st_size
            collected.append((path, content))
    logger.debug("walk_and_collect session_id=%s files=%s total_bytes=%s", session_id, len(collected), total_bytes)
    return collected


def chunk_by_structure(file_path: Path, content: str, repo_root: Path) -> list[dict[str, Any]]:
    """
    Split file into chunks by class/function/top-level block.
    Each chunk: { file, symbols, language, content, start_line }.
    """
    ext = file_path.suffix.lower()
    pattern = SYMBOL_PATTERNS.get(ext)
    rel_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
    language = _language_from_ext(file_path)
    chunks: list[dict[str, Any]] = []

    if not pattern:
        chunks.append({
            "file": rel_path,
            "symbols": [],
            "language": language,
            "content": content,
            "start_line": 1,
        })
        return chunks

    lines = content.splitlines(keepends=True)
    matches = list(pattern.finditer(content))
    if not matches:
        chunks.append({
            "file": rel_path,
            "symbols": [],
            "language": language,
            "content": content,
            "start_line": 1,
        })
        return chunks

    for i, m in enumerate(matches):
        start_byte = m.start()
        end_byte = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        chunk_content = content[start_byte:end_byte]
        start_line = content[:start_byte].count("\n") + 1
        # Extract symbol name from group if available
        groups = m.groups()
        symbol = (groups[2] or groups[-1] or "").strip() if len(groups) >= 2 else ""
        symbols = [symbol] if symbol else []

        chunks.append({
            "file": rel_path,
            "symbols": symbols,
            "language": language,
            "content": chunk_content,
            "start_line": start_line,
        })
    return chunks


def get_embeddings(client: Any, texts: list[str], model: str) -> list[list[float]]:
    """Call OpenAI-compatible embeddings API; return list of vectors."""
    if not texts:
        return []
    resp = client.embeddings.create(input=texts, model=model)
    return [e.embedding for e in resp.data]


def embed_and_index(session_id: str) -> None:
    """
    Walk repo, chunk, embed, upsert to Pinecone with namespace=session_id.
    Uses sync OpenAI client and Pinecone; run in background task or thread.
    """
    from openai import OpenAI
    from pinecone import Pinecone

    repo_root = get_repo_root(session_id)
    logger.info(
        "[reindex] embed_and_index START session_id=%s repo_root=%s exists=%s",
        session_id, repo_root, repo_root.exists(),
    )
    settings = get_settings()
    if not settings.pinecone_api_key or not settings.openai_api_key:
        logger.warning("[reindex] embed_and_index SKIP missing PINECONE_API_KEY or OPENAI_API_KEY")
        return

    file_contents = walk_and_collect(session_id)
    file_paths = [str(p.relative_to(repo_root)) for p, _ in file_contents]
    logger.info(
        "[reindex] embed_and_index walk_and_collect session_id=%s files=%s paths=%s",
        session_id, len(file_contents), file_paths[:15],
    )
    all_chunks: list[dict[str, Any]] = []
    for path, content in file_contents:
        all_chunks.extend(chunk_by_structure(path, content, repo_root))

    if not all_chunks:
        logger.warning("[reindex] embed_and_index no chunks session_id=%s (files=%s)", session_id, len(file_contents))
        return
    logger.info("[reindex] embed_and_index chunking done session_id=%s chunks=%s", session_id, len(all_chunks))

    openai_kwargs: dict = {"api_key": settings.openai_api_key}
    if settings.openai_base_url and settings.openai_base_url.strip():
        openai_kwargs["base_url"] = settings.openai_base_url.strip()
    openai_client = OpenAI(**openai_kwargs)
    texts = [c["content"] for c in all_chunks]
    logger.info("[reindex] embed_and_index calling embeddings model=%s texts=%s", settings.embedding_model, len(texts))
    embeddings = get_embeddings(openai_client, texts, settings.embedding_model)
    logger.info("[reindex] embed_and_index embeddings done session_id=%s vectors=%s", session_id, len(embeddings))

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)
    namespace = session_id
    # Clear namespace so this upsert replaces old content (required for re-index). Serverless only; pod indexes may raise.
    try:
        index.delete_namespace(namespace=namespace)
        logger.info("[reindex] embed_and_index deleted namespace=%s so new content replaces old", namespace)
    except Exception as e:
        err_msg = str(e).lower()
        if "404" in err_msg or "namespace not found" in err_msg:
            logger.debug("[reindex] embed_and_index delete_namespace %s (namespace did not exist)", namespace)
        else:
            logger.warning(
                "[reindex] embed_and_index delete_namespace failed namespace=%s (index may be pod-based; old vectors will remain): %s",
                namespace, e,
            )
    vectors = []
    for i, (chunk, vec) in enumerate(zip(all_chunks, embeddings)):
        chunk_id = str(uuid.uuid4())
        meta = {
            "file": chunk["file"],
            "symbols": "|".join(chunk.get("symbols") or [])[:1000],
            "language": chunk.get("language", ""),
            "start_line": chunk.get("start_line", 0),
        }
        # Store content in metadata if under ~40k (Pinecone metadata limit); otherwise truncate
        content_preview = (chunk["content"] or "")[:30000]
        meta["content"] = content_preview
        vectors.append({"id": chunk_id, "values": vec, "metadata": meta})
        if len(vectors) >= 100:
            index.upsert(vectors=vectors, namespace=namespace)
            logger.info("[reindex] embed_and_index upserted batch 100 namespace=%s", namespace)
            vectors = []

    if vectors:
        index.upsert(vectors=vectors, namespace=namespace)
        logger.info("[reindex] embed_and_index upserted final batch namespace=%s len=%s", namespace, len(vectors))
    logger.info(
        "[reindex] embed_and_index DONE session_id=%s namespace=%s total_chunks=%s",
        session_id, namespace, len(all_chunks),
    )
