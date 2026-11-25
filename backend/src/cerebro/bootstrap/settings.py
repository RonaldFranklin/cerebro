"""Explicit settings of one Cérebro process. Parsing them from the environment or a file is Phase 3."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """Everything the composition root needs; no defaults that point at a real deployment."""

    content_repo: Path
    """Working directory of the (private) content repository: a git repository with ``schema/``."""
    state_dir: Path
    """Directory for ``index.sqlite``, ``state.sqlite`` and the writer lock. Created ``0700``."""
    key_file: Path
    """Fernet key file (``0600``, in a ``0700`` directory), see ADR-0008."""
    git_name: str
    git_email: str
    git_remote: str | None = None
    """Backup remote for ``VersionControl.push``; ``None`` disables publishing."""
    git_branch: str = "main"
    writer_lock_timeout: float = 30.0
    """Seconds a writer waits for the process-wide and cross-process writer lock."""
