"""
Pydantic models for API request/response and LangGraph AgentState.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- API request/response models ---


class CloneRepoRequest(BaseModel):
    """POST /clone-repo body."""

    repo_url: str = Field(..., description="Public GitHub repo URL")


class CloneRepoResponse(BaseModel):
    """Response after cloning; includes session_id and tree."""

    session_id: str
    tree: list[dict[str, Any]]  # Nested file tree
    message: str = "Repo cloned; indexing started in background."


class RepoTreeResponse(BaseModel):
    """GET /repo-tree response."""

    tree: list[dict[str, Any]]


class FileContentResponse(BaseModel):
    """GET /file response."""

    path: str
    content: str


class ApplyPatchRequest(BaseModel):
    """POST /apply-patch body."""

    session_id: str
    diff: str
    dry_run: bool = False


class ApplyPatchResponse(BaseModel):
    """Response after applying patch (or dry_run validation)."""

    success: bool
    message: str
    updated_files: Optional[dict[str, str]] = None  # path -> new content
    error: Optional[str] = None


class RunAgentRequest(BaseModel):
    """POST /run-agent/stream body."""

    session_id: str
    message: str
    search_mode: bool = False


# --- Streaming event types (emitted by graph) ---


class StreamEventStatus(BaseModel):
    type: Literal["status"] = "status"
    message: Optional[str] = None


class StreamEventAgentStep(BaseModel):
    type: Literal["agent_step"] = "agent_step"
    agent: Literal["Planner", "Coder", "Reviewer"]
    message: Optional[str] = None


class StreamEventRetrieval(BaseModel):
    type: Literal["retrieval"] = "retrieval"
    files: list[str] = Field(default_factory=list)


class StreamEventDiff(BaseModel):
    type: Literal["diff"] = "diff"
    diff: str


class ReferenceItem(BaseModel):
    file: str
    line: int


class StreamEventFinal(BaseModel):
    type: Literal["final"] = "final"
    message: Optional[str] = None
    references: list[ReferenceItem] = Field(default_factory=list)


# --- LangGraph AgentState (Pydantic for serialization) ---


class AgentState(BaseModel):
    """Global state for the LangGraph agent; all nodes read/write this."""

    user_input: str = ""
    intent: Literal["chat", "generate", "debug", "search"] = "chat"
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    diff: Optional[str] = None
    error_metadata: Optional[dict[str, Any]] = None
    root_cause: Optional[str] = None
    fix_suggestions: Optional[list[str]] = None
    review_feedback: Optional[str] = None
    approved: bool = False
    changed_files: list[str] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)  # [{file, line}]
    final_response: Optional[str] = None
    review_attempt: int = 0
    session_id: str = ""

    class Config:
        extra = "allow"  # Allow extra fields for graph flexibility

    def to_mutable_dict(self) -> dict[str, Any]:
        """Convert to dict for LangGraph state updates (mutable)."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentState":
        return cls(**{k: v for k, v in d.items() if k in cls.model_fields})
