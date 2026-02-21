"""
Application configuration loaded from environment variables.
"""

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from env; used for API keys, paths, and safety limits."""

    # OpenAI-compatible LLM
    openai_api_key: str = ""
    openai_base_url: str | None = None  # Custom base URL if not OpenAI

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "cursor-clone-index"
    pinecone_env: str | None = None  # For serverless, e.g. us-east-1

    # Workspace
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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    def repo_path(self, session_id: str) -> Path:
        """Path to cloned repo for a session."""
        return self.workspace_root / session_id / "repo"


def get_settings() -> Settings:
    return Settings()
