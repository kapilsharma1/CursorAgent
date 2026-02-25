"""
LangGraph state machine: detect_intent -> retrieve_context -> chat | generate | debug flows.
Streaming via state['stream_events']; reviewer loop; tools for file ops only.
"""

import logging
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from config import get_settings
from retrieval import format_context_for_diff, format_context_for_llm, get_query_embedding, retrieve, retrieve_and_format
from diff_utils import validate_diff
from indexer import embed_and_index
from tools import apply_patch as tool_apply_patch, get_file as tool_get_file, search_symbol, web_search as tool_web_search
from workspace_utils import get_repo_root

logger = logging.getLogger(__name__)

# State: mutable dict. stream_events is appended per node (reducer).
# We use a simple dict schema; LangGraph will merge updates.
def _agent_state_schema() -> dict:
    return {
        "user_input": "",
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
        "session_id": "",
        "stream_events": [],  # list of {type, ...} to send as SSE
        "messages": [],  # LangChain messages for chat model
    }


def _emit(state: dict, *events: dict) -> dict:
    """Append one or more events to stream_events."""
    ev = list(state.get("stream_events") or [])
    ev.extend(events)
    return {"stream_events": ev}


def _llm(system: str, user: str, session_id: str = "") -> str:
    """Sync call to OpenAI-compatible chat; return content string."""
    settings = get_settings()
    if not settings.openai_api_key:
        return ""
    llm_kwargs: dict = {
        "model": "gpt-4o-mini",
        "api_key": settings.openai_api_key,
        "temperature": 0,
    }
    if settings.openai_base_url and settings.openai_base_url.strip():
        llm_kwargs["base_url"] = settings.openai_base_url.strip()
    llm = ChatOpenAI(**llm_kwargs)
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    try:
        logger.debug("_llm invoke system_len=%s user_len=%s", len(system), len(user))
        out = llm.invoke(messages).content
        logger.debug("_llm response_len=%s", len(out or ""))
        return out
    except Exception as e:
        logger.exception("_llm call failed: %s", e)
        return str(e)


async def detect_intent(state: dict) -> dict:
    """Classify user intent: chat | generate | debug. If already 'search' (from client), keep it."""
    if state.get("intent") == "search":
        logger.info("detect_intent session_id=%s intent=search (client)", state.get("session_id"))
        return {**_emit(state, {"type": "status", "message": "Intent: search"})}
    user = (state.get("user_input") or "").strip()
    system = (
        "You are a classifier. Reply with exactly one word: chat, generate, or debug. "
        "Use 'chat' for questions about the codebase, explanations, or navigation. "
        "Use 'generate' when the user wants to create or modify code (add feature, write function, etc.). "
        "Use 'debug' when the user pastes an error, stack trace, or asks why something fails."
    )
    out = _llm(system, user[:2000]).strip().lower()
    intent = "chat"
    if "generate" in out:
        intent = "generate"
    elif "debug" in out:
        intent = "debug"
    logger.info("detect_intent session_id=%s intent=%s raw=%s", state.get("session_id"), intent, out[:50])
    return {"intent": intent, **_emit(state, {"type": "status", "message": f"Intent: {intent}"})}


async def retrieve_context(state: dict) -> dict:
    """Embed user input, retrieve from Pinecone (session namespace), set retrieved_chunks and emit retrieval event."""
    session_id = state.get("session_id") or ""
    query = state.get("user_input") or ""
    # For debug flow we might have been called with error message; use error_metadata if set
    if state.get("error_metadata") and isinstance(state.get("error_metadata"), dict):
        err = state["error_metadata"]
        query = err.get("message") or err.get("text") or query
    chunks, _ = retrieve_and_format(query, session_id, top_k=5)
    files = list({c.get("file") or "" for c in chunks if c.get("file")})
    logger.info("retrieve_context session_id=%s chunks=%s files=%s", session_id, len(chunks), files[:10])
    return {
        "retrieved_chunks": chunks,
        **_emit(state, {"type": "retrieval", "files": files}),
    }


async def chat_reasoner(state: dict) -> dict:
    """Answer using retrieved context; include file/line references. Emit agent_step and final."""
    chunks = state.get("retrieved_chunks") or []
    user = state.get("user_input") or ""
    context = format_context_for_llm(chunks)
    system = (
        "You are a helpful coding assistant. Use only the following code context to answer. "
        "Include file and line references in your answer. "
        "Reply with a clear, concise answer. Do not expose chain-of-thought; only the final answer. "
        "At the end, list references as: File: path, Line: N."
    )
    prompt = f"Context:\n{context}\n\nUser question: {user}"
    response = _llm(system, prompt[:12000], state.get("session_id"))
    refs = state.get("references") or []
    # Simple ref extraction: "File: x, Line: N" or "file path line N"
    for line in response.split("\n"):
        if "line" in line.lower() and "file" in line.lower():
            parts = re.split(r"[,:]", line)
            for i, p in enumerate(parts):
                if "file" in p.lower() and i + 1 < len(parts):
                    f = parts[i + 1].strip().strip(".")
                    if i + 3 < len(parts) and "line" in parts[i + 2].lower():
                        try:
                            ln = int(parts[i + 3].strip())
                            refs.append({"file": f, "line": ln})
                        except ValueError:
                            pass
                    break
    logger.info("chat_reasoner session_id=%s response_len=%s refs=%s", state.get("session_id"), len(response), len(refs))
    return {
        "final_response": response,
        "references": refs,
        **_emit(state, {"type": "agent_step", "agent": "Planner", "message": "Answered from context."}, {"type": "final", "message": response, "references": refs}),
    }


async def planner(state: dict) -> dict:
    """Produce a short plan (list of steps) for code generation."""
    chunks = state.get("retrieved_chunks") or []
    user = state.get("user_input") or ""
    context = format_context_for_llm(chunks)
    system = (
        "You are a coding planner. Given the user request and code context, output a short numbered plan (3-5 steps). "
        "Only output the plan, no other text. One step per line."
    )
    out = _llm(system, f"Context:\n{context}\n\nRequest: {user}", state.get("session_id"))
    plan = [s.strip() for s in out.split("\n") if s.strip() and s.strip()[0].isdigit()]
    if not plan:
        plan = [out.strip()] if out.strip() else ["Implement the requested change."]
    logger.info("planner session_id=%s steps=%s", state.get("session_id"), len(plan))
    return {
        "plan": plan,
        **_emit(state, {"type": "agent_step", "agent": "Planner", "message": "Plan: " + "; ".join(plan[:3])}),
    }


def _build_coder_context_with_current_files(chunks: list[dict], session_id: str) -> str:
    """
    Build context for the coder using current file content from the workspace (not just indexed chunks)
    so the diff matches the exact current state of each file. Each line is prefixed with "  N| ".
    """
    if not chunks:
        return ""
    seen_files: set[str] = set()
    parts = []
    for c in chunks:
        file_path = c.get("file", "")
        if not file_path or file_path in seen_files:
            continue
        seen_files.add(file_path)
        symbols = c.get("symbols") or []
        # Prefer current file content from workspace so diff matches on-disk state
        current = tool_get_file(file_path, session_id) if session_id else None
        content = current if current is not None else (c.get("content") or "")
        start_line = 1  # full file is always 1-based
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
    return "\n\n---\n\n".join(parts) if parts else format_context_for_diff(chunks)


async def coder(state: dict) -> dict:
    """Produce a unified diff only. Use plan and retrieved context with line numbers so the diff matches the real file."""
    chunks = state.get("retrieved_chunks") or []
    plan = state.get("plan") or []
    user = state.get("user_input") or ""
    session_id = state.get("session_id") or ""
    context = _build_coder_context_with_current_files(chunks, session_id)
    system = (
        "You are a code generator. Output ONLY a valid unified diff (e.g. --- a/file\n+++ b/file\n@@ ... @@). "
        "No explanation, no markdown, no code block wrapper. Just the raw diff. "
        "CRITICAL: The context shows file content with line numbers (N|). Your diff MUST use these EXACT line numbers in the @@ hunk headers. "
        "Example: if the line you are changing is shown as '  2| print(\"x\");' then use @@ -2,1 +2,1 @@ for that hunk, not @@ -1,1 +1,1 @@. "
        "Do not assume line 1 is the first line of code—use the line numbers shown. Match the file exactly; do not add or remove leading/trailing blank lines unless they appear in the context. "
        "Make minimal, correct changes. Use the plan and context below."
    )
    prompt = f"Plan:\n" + "\n".join(plan) + f"\n\nContext:\n{context}\n\nRequest: {user}"
    diff = _llm(system, prompt[:14000], state.get("session_id"))
    # Strip markdown code block if present
    if "```" in diff:
        for sep in ("```diff", "```\n", "```"):
            if sep in diff:
                i = diff.find(sep)
                diff = diff[i + len(sep):]
                if diff.endswith("```"):
                    diff = diff[:-3]
                break
    diff = diff.strip()
    logger.info("coder session_id=%s diff_len=%s", state.get("session_id"), len(diff))
    return {
        "diff": diff,
        **_emit(state, {"type": "agent_step", "agent": "Coder", "message": "Produced diff."}, {"type": "diff", "diff": diff}),
    }


async def validate_diff_node(state: dict) -> dict:
    """Deterministic validation. Set approved=True if valid else leave False and loop back to coder."""
    diff = state.get("diff") or ""
    session_id = state.get("session_id") or ""
    repo_root = get_repo_root(session_id)
    if not repo_root.exists():
        return {**_emit(state, {"type": "status", "message": "Repo not found; diff invalid."})}
    ok, err = validate_diff(diff, repo_root)
    logger.info("validate_diff_node session_id=%s ok=%s err=%s", session_id, ok, err or "(none)")
    if ok:
        return {"approved": True, **_emit(state, {"type": "status", "message": "Diff valid."})}
    return {**_emit(state, {"type": "status", "message": f"Diff invalid: {err}"})}


async def apply_patch_node(state: dict) -> dict:
    """Call apply_patch tool; update state with changed_files. Re-index in same thread so read sees written content."""
    diff = state.get("diff") or ""
    session_id = state.get("session_id") or ""
    result = tool_apply_patch(diff, session_id)
    if not result.get("success"):
        logger.warning("apply_patch_node failed session_id=%s: %s", session_id, result.get("error"))
        return {**_emit(state, {"type": "status", "message": result.get("error", "Apply failed.")})}
    updated = result.get("updated_files") or {}
    logger.info(
        "apply_patch_node session_id=%s updated=%s; re-indexing in same thread",
        session_id, list(updated.keys()),
    )
    embed_and_index(session_id)
    return {
        "changed_files": list(updated.keys()),
        **_emit(state, {"type": "status", "message": f"Applied to {list(updated.keys())}."}),
    }


async def reviewer(state: dict) -> dict:
    """Review applied change or diff; set approved True/False and review_attempt. Debug flow (no diff, fix_suggestions) is auto-approved."""
    diff = state.get("diff") or ""
    changed = state.get("changed_files") or []
    attempt = state.get("review_attempt") or 0
    fix_suggestions = state.get("fix_suggestions")
    # Debug flow: no diff, only text suggestion -> approve and go to final
    if not diff and fix_suggestions:
        return {
            "approved": True,
            "review_attempt": attempt,
            **_emit(state, {"type": "agent_step", "agent": "Reviewer", "message": "Approved suggestion."}),
        }
    system = (
        "You are a code reviewer. Check the diff for: missing imports, syntax issues, wrong variable names. "
        "Reply with exactly 'APPROVED' or 'REJECTED' on the first line, then optionally one line of feedback."
    )
    content = f"Diff:\n{diff[:8000]}\n\nChanged files: {changed}"
    out = _llm(system, content, state.get("session_id")).strip().upper()
    approved = "APPROVED" in out.split("\n")[0]
    feedback = out if not approved else ""
    if not approved:
        attempt += 1
    logger.info("reviewer session_id=%s approved=%s attempt=%s", state.get("session_id"), approved, attempt)
    return {
        "approved": approved,
        "review_feedback": feedback,
        "review_attempt": attempt,
        **_emit(state, {"type": "agent_step", "agent": "Reviewer", "message": "APPROVED" if approved else "REJECTED: " + feedback[:200]}),
    }


async def parse_error(state: dict) -> dict:
    """Extract stack trace / error message from user_input into error_metadata."""
    user = state.get("user_input") or ""
    # Simple extraction: treat as one block of error text
    error_metadata = {"message": user[:4000], "text": user[:4000]}
    logger.info("parse_error session_id=%s message_len=%s", state.get("session_id"), len(user))
    return {
        "error_metadata": error_metadata,
        **_emit(state, {"type": "status", "message": "Parsed error for analysis."}),
    }


async def retrieve_context_debug(state: dict) -> dict:
    """Targeted retrieval using error message (for debug flow)."""
    err = state.get("error_metadata") or {}
    query = err.get("message") or err.get("text") or state.get("user_input") or ""
    session_id = state.get("session_id") or ""
    chunks, _ = retrieve_and_format(query[:3000], session_id, top_k=5)
    files = list({c.get("file") or "" for c in chunks if c.get("file")})
    return {
        "retrieved_chunks": chunks,
        **_emit(state, {"type": "retrieval", "files": files}),
    }


async def root_cause_analysis(state: dict) -> dict:
    """LLM analyzes error_metadata + retrieved context -> root_cause."""
    chunks = state.get("retrieved_chunks") or []
    err = state.get("error_metadata") or {}
    context = format_context_for_llm(chunks)
    system = "You are a debugger. Given the error and code context, identify the root cause in 1-3 sentences. No chain-of-thought."
    prompt = f"Error:\n{err.get('message', '')}\n\nContext:\n{context}"
    root_cause = _llm(system, prompt[:10000], state.get("session_id"))
    logger.info("root_cause_analysis session_id=%s root_cause_len=%s", state.get("session_id"), len(root_cause))
    return {
        "root_cause": root_cause,
        **_emit(state, {"type": "agent_step", "agent": "Planner", "message": "Root cause: " + root_cause[:150]}),
    }


async def suggest_fix(state: dict) -> dict:
    """LLM suggests fix (text). Optionally can produce diff later."""
    root = state.get("root_cause") or ""
    chunks = state.get("retrieved_chunks") or []
    context = format_context_for_llm(chunks)
    system = "You are a debugger. Suggest a concrete fix (steps or code snippet). Be concise. No chain-of-thought."
    prompt = f"Root cause:\n{root}\n\nContext:\n{context}"
    fix = _llm(system, prompt[:10000], state.get("session_id"))
    suggestions = [fix] if fix else []
    logger.info("suggest_fix session_id=%s suggestions=%s", state.get("session_id"), len(suggestions))
    return {
        "fix_suggestions": suggestions,
        **_emit(state, {"type": "agent_step", "agent": "Reviewer", "message": "Fix suggested."}),
    }


async def final_response(state: dict) -> dict:
    """Format final_response and emit final event."""
    resp = state.get("final_response") or ""
    refs = state.get("references") or []
    if not resp and state.get("fix_suggestions"):
        resp = "\n".join(state.get("fix_suggestions") or [])
    if not resp and state.get("root_cause"):
        resp = "Root cause: " + (state.get("root_cause") or "")
    if not resp and state.get("changed_files"):
        changed = state.get("changed_files") or []
        if len(changed) == 1:
            resp = f"Applied changes to {changed[0]}."
        else:
            resp = f"Applied changes to {len(changed)} file(s): " + ", ".join(changed)
    return {
        "final_response": resp,
        **_emit(state, {"type": "final", "message": resp, "references": refs}),
    }


def _route_after_detect(state: dict) -> Literal["search_web", "retrieve_context"]:
    """Route after detect_intent: search goes to search_web, else to retrieve_context."""
    if state.get("intent") == "search":
        return "search_web"
    return "retrieve_context"


def _route_after_retrieve(state: dict) -> Literal["chat_reasoner", "planner", "parse_error"]:
    intent = state.get("intent") or "chat"
    if intent == "generate":
        return "planner"
    if intent == "debug":
        return "parse_error"
    return "chat_reasoner"


async def search_web(state: dict) -> dict:
    """Run Tavily web search on user_input; format results and set final_response, then go to final_response."""
    query = (state.get("user_input") or "").strip()
    out = {}
    results = tool_web_search(query)
    if not results:
        resp = "No web results found."
    else:
        parts = []
        for i, r in enumerate(results[:8], 1):
            title = (r.get("title") or "Result").strip()
            url = (r.get("url") or "").strip()
            content = (r.get("content") or "").strip()
            if url and not content.startswith("http"):
                parts.append(f"{i}. **{title}**\n   {url}\n   {content[:500]}")
            else:
                parts.append(f"{i}. **{title}**\n   {content[:500]}")
        raw = "\n\n".join(parts)
        # Optional: summarize with LLM for a concise answer
        settings = get_settings()
        if settings.openai_api_key and len(raw) > 500:
            system = (
                "You are a helpful assistant. Given web search results below, answer the user's question concisely. "
                "Use markdown. Keep under 400 words. If results mention code or steps, include the key points."
            )
            user_msg = f"User question: {query}\n\nSearch results:\n{raw[:6000]}"
            resp = _llm(system, user_msg, state.get("session_id") or "")
            if not resp or resp.startswith("Error") or resp.startswith("Exception"):
                resp = raw
        else:
            resp = raw
    out["final_response"] = resp
    out["references"] = []
    return out


def _route_after_validate(state: dict) -> Literal["coder", "apply_patch_node"]:
    if state.get("approved"):
        return "apply_patch_node"
    return "coder"


def _route_after_reviewer(state: dict) -> Literal["coder", "final_response"]:
    if state.get("approved"):
        return "final_response"
    if (state.get("review_attempt") or 0) >= 2:
        return "final_response"
    return "coder"


def build_graph():
    """Build and compile the LangGraph state machine."""
    from typing import TypedDict, Any, Optional

    class State(TypedDict, total=False):
        user_input: str
        intent: str
        retrieved_chunks: list
        plan: list
        diff: Optional[str]
        error_metadata: Optional[dict]
        root_cause: Optional[str]
        fix_suggestions: Optional[list]
        review_feedback: Optional[str]
        approved: bool
        changed_files: list
        references: list
        final_response: Optional[str]
        review_attempt: int
        session_id: str
        stream_events: list
        messages: list

    builder = StateGraph(State)

    builder.add_node("detect_intent", detect_intent)
    builder.add_node("search_web", search_web)
    builder.add_node("retrieve_context", retrieve_context)
    builder.add_node("chat_reasoner", chat_reasoner)
    builder.add_node("planner", planner)
    builder.add_node("coder", coder)
    builder.add_node("validate_diff", validate_diff_node)
    builder.add_node("apply_patch_node", apply_patch_node)
    builder.add_node("reviewer", reviewer)
    builder.add_node("parse_error", parse_error)
    builder.add_node("retrieve_context_debug", retrieve_context_debug)
    builder.add_node("root_cause_analysis", root_cause_analysis)
    builder.add_node("suggest_fix", suggest_fix)
    builder.add_node("final_response", final_response)

    builder.set_entry_point("detect_intent")
    builder.add_conditional_edges("detect_intent", _route_after_detect, {"search_web": "search_web", "retrieve_context": "retrieve_context"})
    builder.add_edge("search_web", "final_response")
    builder.add_conditional_edges("retrieve_context", _route_after_retrieve)

    builder.add_edge("chat_reasoner", "final_response")
    builder.add_edge("final_response", END)

    builder.add_edge("planner", "coder")
    builder.add_edge("coder", "validate_diff")
    builder.add_conditional_edges("validate_diff", _route_after_validate)
    builder.add_edge("apply_patch_node", "reviewer")
    builder.add_conditional_edges("reviewer", _route_after_reviewer, {"coder": "coder", "final_response": "final_response"})

    builder.add_edge("parse_error", "retrieve_context_debug")
    builder.add_edge("retrieve_context_debug", "root_cause_analysis")
    builder.add_edge("root_cause_analysis", "suggest_fix")
    builder.add_edge("suggest_fix", "reviewer")  # reviewer for debug reviews suggestion
    # From suggest_fix we go to reviewer; reviewer then goes to final_response (we don't loop to coder in debug for patch)
    # So we need reviewer in debug to go to final_response. Our _route_after_reviewer: if approved -> final_response, else if attempt>=2 -> final_response, else coder.
    # For debug flow, we don't have a diff from coder; suggest_fix only set fix_suggestions. So reviewer gets diff="" and changed_files=[]. So reviewer will likely "APPROVE" the text suggestion. Then we go to final_response. So we need final_response to pick up fix_suggestions when final_response is empty. We already did that in final_response node.

    return builder.compile()


# Singleton compiled graph
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        logger.debug("Building LangGraph (first call)")
        _graph = build_graph()
    return _graph
