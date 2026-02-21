"""
FastAPI application: clone, repo tree, file, run-agent/stream (SSE), apply-patch.
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.config import get_settings
from backend.github import clone_repo
from backend.graph import get_graph
from backend.indexer import embed_and_index
from backend.models import (
    ApplyPatchRequest,
    ApplyPatchResponse,
    CloneRepoRequest,
    CloneRepoResponse,
    FileContentResponse,
    RepoTreeResponse,
    RunAgentRequest,
)
from backend.workspace_utils import build_file_tree, get_repo_root, read_file_content, resolve_file_path
from backend.diff_utils import validate_diff
from backend.diff_utils import apply_patch as apply_patch_impl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure workspace exists. Shutdown: nothing."""
    settings = get_settings()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Cursor-like AI Assistant API",
    description="Clone repos, index with Pinecone, run LangGraph agent with SSE streaming.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok"}


def run_indexing(session_id: str) -> None:
    """Run indexing in background (sync; call from thread or run_in_executor)."""
    try:
        embed_and_index(session_id)
    except Exception as e:
        logger.exception("Indexing failed for %s: %s", session_id, e)


@app.post("/clone-repo", response_model=CloneRepoResponse)
async def post_clone_repo(body: CloneRepoRequest, background_tasks: BackgroundTasks):
    """Clone public GitHub repo; return session_id and file tree. Start indexing in background."""
    session_id = str(uuid.uuid4())
    settings = get_settings()
    repo_path = settings.repo_path(session_id)
    try:
        await clone_repo(body.repo_url, repo_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    tree = build_file_tree(repo_path)
    background_tasks.add_task(lambda: run_indexing(session_id))
    return CloneRepoResponse(session_id=session_id, tree=tree)


@app.get("/repo-tree", response_model=RepoTreeResponse)
async def get_repo_tree(session_id: str):
    """Return file tree for the cloned repo."""
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        raise HTTPException(status_code=404, detail="Repo not found for this session.")
    tree = build_file_tree(repo_root)
    return RepoTreeResponse(tree=tree)


@app.get("/file", response_model=FileContentResponse)
async def get_file(session_id: str, path: str):
    """Return file content. Path relative to repo root; no traversal."""
    resolved = resolve_file_path(session_id, path)
    if not resolved:
        raise HTTPException(status_code=404, detail="File not found or access denied.")
    content = read_file_content(resolved)
    if content is None:
        raise HTTPException(status_code=400, detail="File too large or binary.")
    return FileContentResponse(path=path, content=content)


@app.post("/run-agent/stream")
async def run_agent_stream(body: RunAgentRequest):
    """Run LangGraph agent; stream structured SSE events (status, agent_step, retrieval, diff, final)."""
    session_id = body.session_id
    message = body.message or ""
    graph = get_graph()
    initial_state = {
        "user_input": message,
        "session_id": session_id,
        "intent": "chat",
        "retrieved_chunks": [],
        "plan": [],
        "diff": None,
        "error_metadata": None,
        "root_cause": None,
        "fix_suggestions": None,
        "review_feedback": None,
        "approved": False,
        "changed_files": [],
        "references": [],
        "final_response": None,
        "review_attempt": 0,
        "stream_events": [],
        "messages": [],
    }

    async def event_generator():
        sent_count = 0
        try:
            async for event in graph.astream(initial_state, stream_mode="updates"):
                for _node_name, update in event.items():
                    events = update.get("stream_events") or []
                    for i in range(sent_count, len(events)):
                        yield f"data: {json.dumps(events[i])}\n\n"
                    sent_count = len(events)
        except Exception as e:
            logger.exception("Agent stream error: %s", e)
            yield f"data: {json.dumps({'type': 'status', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/apply-patch", response_model=ApplyPatchResponse)
async def apply_patch(body: ApplyPatchRequest):
    """Validate and optionally apply patch. Safe-by-default; dry_run only validates."""
    session_id = body.session_id
    diff = body.diff or ""
    dry_run = body.dry_run
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        return ApplyPatchResponse(success=False, message="Repo not found.", error="Repo not found for session.")
    ok, err = validate_diff(diff, repo_root)
    if not ok:
        return ApplyPatchResponse(success=False, message="Validation failed.", error=err)
    if dry_run:
        return ApplyPatchResponse(success=True, message="Diff is valid (dry run).")
    result = apply_patch_impl(diff, repo_root)
    if isinstance(result, str):
        return ApplyPatchResponse(success=False, message="Apply failed.", error=result)
    return ApplyPatchResponse(success=True, message="Patch applied.", updated_files=result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
