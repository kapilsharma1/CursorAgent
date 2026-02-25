"""
Application configuration loaded from environment variables.
"""

from pathlib import Path
from typing import List

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Backend package directory (where config.py lives). Used to resolve relative workspace_root.
_BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Settings loaded from env; used for API keys, paths, and safety limits."""

    # OpenAI-compatible LLM
    openai_api_key: str = ""
    openai_base_url: str | None = None  # Custom base URL if not OpenAI

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "cursor-clone-index"
    pinecone_env: str | None = None  # For serverless, e.g. us-east-1

    # Tavily (web search)
    tavily_api_key: str = ""

    # Workspace (relative paths are resolved against the backend directory so clone and apply-patch use the same path)
    workspace_root: Path = Path("./workspace")

    # Safety limits
    max_repo_bytes: int = 10_485_760  # 10MB
    max_file_bytes: int = 512_000  # 500KB
    max_files_in_patch: int = 5
    max_patch_lines: int = 500

    # Indexing
    index_extensions: List[str] = [
        ".js", ".ts", ".tsx", ".py", ".java", ".go", ".cpp", ".c"
    ]
    ignore_dirs: List[str] = [
        ".git", "node_modules", "dist", "target", "build", "__pycache__"
    ]

    # Embedding
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536  # Match model

    # Logging (DEBUG, INFO, WARNING, ERROR)
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @model_validator(mode="after")
    def resolve_workspace_root(self) -> "Settings":
        """Resolve relative workspace_root to an absolute path so it does not depend on process cwd."""
        if not self.workspace_root.is_absolute():
            self.workspace_root = (_BACKEND_DIR / self.workspace_root).resolve()
        return self

    def repo_path(self, session_id: str) -> Path:
        """Path to cloned repo for a session."""
        return self.workspace_root / session_id / "repo"


def get_settings() -> Settings:
    return Settings()
