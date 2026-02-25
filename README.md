# Cursor-like AI Coding Assistant MVP

A production-quality MVP that implements a Cursor-style AI coding assistant: clone public GitHub repos, index code with **Pinecone**, run a **LangGraph** state machine (chat / generate / debug), and stream structured events over **SSE**. The frontend uses **React**, **Vite**, and **Monaco Editor** with diff preview and accept/reject.

## Architecture

- **Frontend**: React + Vite + TypeScript, Monaco Editor, SSE client. Layout: File tree (left) | Code editor (center) | Chat + collapsible reasoning (right).
- **Backend**: FastAPI, async endpoints. Per-session workspace under `workspace/{session_id}/repo`.
- **RAG**: Code is chunked by structure (class/function), embedded (OpenAI-compatible), and stored in **Pinecone** with one **namespace per session** (`namespace=session_id`). No local vector files.
- **Orchestration**: A single **LangGraph** state machine with conditional edges: intent → retrieve → **chat** | **generate** | **debug**. Generate flow: planner → coder (unified diff) → validate → apply (tool) → reviewer → loop back to coder if not approved (max 2). Debug flow: parse_error → targeted retrieve → root_cause_analysis → suggest_fix → reviewer → final.
- **Streaming**: All graph nodes push structured events into state (`stream_events`). The `/run-agent/stream` endpoint streams these as SSE (status, agent_step, retrieval, diff, final). No raw chain-of-thought.

## Why LangGraph

- **State machine**: One shared state object; nodes read/write it; conditional edges implement branching (chat vs generate vs debug) and the reviewer → coder loop.
- **Deterministic vs LLM**: Validation and path checks are deterministic; only planning, coding, and review call the LLM. Tools (search_symbol, get_file, apply_patch) are backend functions; the LLM never edits the filesystem directly.
- **Production-friendly**: Clear separation of intent, retrieval, and flows; easy to add retries, timeouts, or new nodes.

## Why diff-based editing

- **Auditable**: Every change is a unified diff; the user sees exactly what will be applied.
- **Reversible**: Accept/Reject in the UI; backend validates (path traversal, file count, line count, blocklist) before apply.
- **Safe-by-default**: Patches are applied only after explicit user Accept; no automatic multi-file edits.

## RAG over code

- **Chunking**: By symbol (class, function, top-level block) per language (e.g. `^class`, `^def`, `^function`). Metadata: file path, symbols, language.
- **Embeddings**: OpenAI-compatible API; vectors stored in Pinecone with session namespace.
- **Retrieval**: Query by namespace; top-k chunks formatted as `File: ... Symbols: ... Code: ...` for the LLM.

## Agent orchestration

- **Chat**: retrieve_context → chat_reasoner → final_response (with file/line references).
- **Generate**: planner → coder (unified diff) → validate_diff → apply_patch (tool) → reviewer → conditional loop to coder (if not approved, max 2) or final_response.
- **Debug**: parse_error → retrieve_context (targeted) → root_cause_analysis → suggest_fix → reviewer → final_response.

## Streaming UX

- SSE events: `status`, `agent_step` (Planner/Coder/Reviewer), `retrieval` (files), `diff`, `final` (message + references).
- UI: live status bar (current agent), collapsible reasoning panel (events only), chat with final answer and references, diff modal with Accept/Reject, line highlighting and scroll-to-line in Monaco for references.

## Safety

- **Dry-run by default**: Patch is applied only when the user clicks Accept in the DiffViewer.
- **Limits**: Max repo size 10MB, max file 500KB, max 5 files and 500 lines per patch; blocklisted paths (e.g. `.env`); no path traversal.
- **No shell from LLM**: Only tools `search_symbol`, `get_file`, `apply_patch`; `apply_patch` validates server-side.

## Future improvements

- OAuth for private GitHub repos.
- Git push (commit and push applied changes).
- Larger repo support and incremental indexing.

---

## Quick start

### Backend

```bash
cd backend
pip install -r requirements.txt
# Set env (see below)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the app (e.g. http://localhost:5173). Use the API proxy: in `vite.config.ts`, `/api` is proxied to `http://localhost:8000`, so frontend calls go to `/api/...`.

### Environment

Create `backend/.env`:

```env
OPENAI_API_KEY=''
PINECONE_API_KEY=''
PINECONE_INDEX_NAME=''
# Optional:
OPENAI_BASE_URL=https://...
WORKSPACE_ROOT=./workspace
MAX_REPO_BYTES=10485760
MAX_FILE_BYTES=512000
MAX_FILES_IN_PATCH=10
MAX_PATCH_LINES=2000
# Logging: DEBUG, INFO, WARNING, ERROR (default: INFO)
LOG_LEVEL=INFO
```

Create a Pinecone index with dimension matching your embedding model (e.g. **1536** for `text-embedding-3-small`). The app uses **namespaces** per session; one index is enough.

### Frontend env

Optional: `frontend/.env`:

```env
VITE_API_URL=http://localhost:8000
# Set to 'true' or '1' for verbose console logs (e.g. in production build)
VITE_DEBUG=false
```

If not set, the app uses `/api` (relative), which works with the Vite proxy.

### Logging (debugging)

- **Backend**: Logging is configured in `log_config.py` and used across all modules. Set `LOG_LEVEL=DEBUG` in `backend/.env` for verbose logs (default: `INFO`). Each request is logged (method, path, status); clone, index, agent stream, and apply-patch are logged with session IDs and outcomes.
- **Frontend**: A small logger in `src/lib/logger.ts` writes to the browser console with a `[CursorClone]` prefix. In development, `info`/`debug` are enabled; in production they are off unless you set `VITE_DEBUG=true` in `frontend/.env`. Errors and warnings are always logged.

---

## Project structure

```
backend/
  main.py       # FastAPI: clone, repo-tree, file, run-agent/stream (SSE), apply-patch
  graph.py      # LangGraph state machine (intent, retrieve, chat/generate/debug, reviewer loop)
  tools.py      # search_symbol, get_file, apply_patch
  github.py     # clone public GitHub repo
  indexer.py    # walk, chunk, embed, Pinecone upsert (namespace=session_id)
  retrieval.py  # query Pinecone, format context
  diff_utils.py # parse, validate, apply unified diff
  models.py     # Pydantic + AgentState
  config.py     # Settings from env
  workspace_utils.py  # file tree, safe path resolution
frontend/
  src/
    components/ # RepoLoader, FileTree, CodeEditor, ChatPanel, DiffViewer, ReasoningPanel, AgentStatusBar
    api/agent.ts
    App.tsx, main.tsx
```

---

## License

MIT.
