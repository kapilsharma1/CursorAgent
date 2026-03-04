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

from config import get_settings
from github import clone_repo, create_session_branch, normalize_github_url
from git_ops import commit_and_push
from graph import get_graph
from workspace_utils import save_session_meta, session_branch_name
from indexer import embed_and_index
from models import (
    ApplyPatchRequest,
    ApplyPatchResponse,
    CloneRepoRequest,
    CloneRepoResponse,
    CommitAndPushRequest,
    CommitAndPushResponse,
    FileContentResponse,
    RepoTreeResponse,
    RunAgentRequest,
)
from workspace_utils import build_file_tree, get_repo_root, read_file_content, resolve_file_path
from diff_utils import validate_diff
from diff_utils import apply_patch as apply_patch_impl
from log_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure workspace exists. Shutdown: nothing."""
    settings = get_settings()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    logger.info("Lifespan startup: workspace_root=%s", settings.workspace_root)
    yield
    logger.info("Lifespan shutdown")


app = FastAPI(
    title="AutoCode By Kapil API",
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


@app.middleware("http")
async def log_requests(request, call_next):
    """Log every request and response status for debugging."""
    logger.info("Request started %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        logger.info("Request finished %s %s -> %s", request.method, request.url.path, response.status_code)
        return response
    except Exception as e:
        logger.exception("Request failed %s %s: %s", request.method, request.url.path, e)
        raise


@app.get("/health")
async def health():
    """Health check."""
    logger.debug("GET /health")
    return {"status": "ok"}


def run_indexing(session_id: str) -> None:
    """Run indexing in background (sync; call from thread or run_in_executor)."""
    repo_root = get_repo_root(session_id)
    logger.info(
        "[reindex] run_indexing ENTRY session_id=%s repo_root=%s exists=%s",
        session_id, repo_root, repo_root.exists(),
    )
    try:
        embed_and_index(session_id)
        logger.info("[reindex] run_indexing DONE session_id=%s", session_id)
    except Exception as e:
        logger.exception("[reindex] run_indexing FAILED session_id=%s: %s", session_id, e)


@app.post("/clone-repo", response_model=CloneRepoResponse)
async def post_clone_repo(body: CloneRepoRequest, background_tasks: BackgroundTasks):
    """Clone public GitHub repo; return session_id and file tree. Start indexing in background."""
    session_id = str(uuid.uuid4())
    logger.info("POST /clone-repo repo_url=%s -> session_id=%s", body.repo_url, session_id)
    settings = get_settings()
    repo_path = settings.repo_path(session_id)
    try:
        await clone_repo(body.repo_url, repo_path)
    except ValueError as e:
        logger.warning("Clone validation failed repo_url=%s: %s", body.repo_url, e)
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("Clone runtime error repo_url=%s: %s", body.repo_url, e)
        raise HTTPException(status_code=502, detail=str(e))

    session_branch = session_branch_name(session_id)
    try:
        await create_session_branch(repo_path, session_branch)
    except RuntimeError as e:
        logger.error("Create session branch failed session_id=%s: %s", session_id, e)
        raise HTTPException(status_code=502, detail=f"Failed to create session branch: {e}")

    try:
        normalized_url = normalize_github_url(body.repo_url)
    except ValueError:
        normalized_url = body.repo_url.strip()
    save_session_meta(session_id, normalized_url, session_branch)

    tree = build_file_tree(repo_path)
    logger.debug("Clone tree built entries=%s", len(tree))
    background_tasks.add_task(lambda: run_indexing(session_id))
    logger.info("POST /clone-repo success session_id=%s branch=%s", session_id, session_branch)
    return CloneRepoResponse(session_id=session_id, tree=tree)


@app.get("/repo-tree", response_model=RepoTreeResponse)
async def get_repo_tree(session_id: str):
    """Return file tree for the cloned repo."""
    logger.debug("GET /repo-tree session_id=%s", session_id)
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        logger.warning("GET /repo-tree repo not found session_id=%s", session_id)
        raise HTTPException(status_code=404, detail="Repo not found for this session.")
    tree = build_file_tree(repo_root)
    logger.debug("GET /repo-tree success session_id=%s entries=%s", session_id, len(tree))
    return RepoTreeResponse(tree=tree)


@app.get("/file", response_model=FileContentResponse)
async def get_file(session_id: str, path: str):
    """Return file content. Path relative to repo root; no traversal."""
    logger.debug("GET /file session_id=%s path=%s", session_id, path)
    resolved = resolve_file_path(session_id, path)
    if not resolved:
        logger.warning("GET /file not found session_id=%s path=%s", session_id, path)
        raise HTTPException(status_code=404, detail="File not found or access denied.")
    content = read_file_content(resolved)
    if content is None:
        logger.warning("GET /file unreadable (large/binary) session_id=%s path=%s", session_id, path)
        raise HTTPException(status_code=400, detail="File too large or binary.")
    logger.debug("GET /file success session_id=%s path=%s len=%s", session_id, path, len(content))
    return FileContentResponse(path=path, content=content)


@app.post("/run-agent/stream")
async def run_agent_stream(body: RunAgentRequest):
    """Run LangGraph agent; stream structured SSE events (status, agent_step, retrieval, diff, final)."""
    session_id = body.session_id
    message = body.message or ""
    logger.info("POST /run-agent/stream session_id=%s message_len=%s", session_id, len(message))
    graph = get_graph()
    initial_state = {
        "user_input": message,
        "session_id": session_id,
        "intent": "search" if body.search_mode else "chat",
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
            if body.search_mode:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Searching the web…'})}\n\n"
            async for event in graph.astream(initial_state, stream_mode="updates"):
                for node_name, update in event.items():
                    events = update.get("stream_events") or []
                    for i in range(sent_count, len(events)):
                        yield f"data: {json.dumps(events[i])}\n\n"
                    if events:
                        logger.debug("Agent node=%s emitted %s new events", node_name, len(events) - sent_count)
                    sent_count = len(events)
            logger.info("POST /run-agent/stream completed session_id=%s total_events=%s", session_id, sent_count)
        except Exception as e:
            logger.exception("Agent stream error session_id=%s: %s", session_id, e)
            yield f"data: {json.dumps({'type': 'status', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/apply-patch", response_model=ApplyPatchResponse)
async def apply_patch(body: ApplyPatchRequest):
    """Validate and optionally apply patch. Safe-by-default; dry_run only validates. Re-indexes after apply (same thread) so chat sees updated content."""
    session_id = body.session_id
    diff = body.diff or ""
    dry_run = body.dry_run
    logger.info("POST /apply-patch session_id=%s dry_run=%s diff_len=%s", session_id, dry_run, len(diff))
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        logger.warning("POST /apply-patch repo not found session_id=%s", session_id)
        return ApplyPatchResponse(success=False, message="Repo not found.", error="Repo not found for session.")
    ok, err = validate_diff(diff, repo_root)
    if not ok:
        logger.warning("POST /apply-patch validation failed session_id=%s: %s", session_id, err)
        return ApplyPatchResponse(success=False, message="Validation failed.", error=err)
    if dry_run:
        logger.info("POST /apply-patch dry_run valid session_id=%s", session_id)
        return ApplyPatchResponse(success=True, message="Diff is valid (dry run).")
    result = apply_patch_impl(diff, repo_root)
    if isinstance(result, str):
        logger.error("POST /apply-patch apply failed session_id=%s: %s", session_id, result)
        return ApplyPatchResponse(success=False, message="Apply failed.", error=result)
    logger.info(
        "POST /apply-patch success session_id=%s updated_files=%s; re-indexing in same thread",
        session_id, list(result.keys()),
    )
    run_indexing(session_id)
    return ApplyPatchResponse(success=True, message="Patch applied.", updated_files=result)


@app.post("/commit-and-push", response_model=CommitAndPushResponse)
async def post_commit_and_push(body: CommitAndPushRequest):
    """Commit all changes and push to session branch. Use force=True after non-fast-forward to overwrite remote."""
    session_id = body.session_id
    commit_message = body.commit_message or "Updates from Cursor Clone"
    force = body.force
    logger.info("POST /commit-and-push session_id=%s force=%s commit_message=%s", session_id, force, commit_message)
    result = await commit_and_push(session_id, commit_message=commit_message, force=force)
    logger.info(
        "commit-and-push result session_id=%s success=%s message=%s error=%s force_required=%s",
        session_id,
        result["success"],
        result.get("message"),
        result.get("error"),
        result.get("force_required", False),
    )
    return CommitAndPushResponse(
        success=result["success"],
        message=result["message"],
        error=result.get("error"),
        force_required=result.get("force_required", False),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
