"""Explicit settings of one Cérebro process. Environment overrides take precedence over an explicit TOML file."""

import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cerebro.adapters.outbound.git import GitError, GitIdentity, validate_branch, validate_remote
from cerebro.domain.proposal import ProposalLimits


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
    writes_per_hour: int = 20
    proposal_limits: ProposalLimits = field(default_factory=ProposalLimits)
    git_remote: str | None = field(default=None, repr=False)
    """Backup remote for ``VersionControl.push``; ``None`` disables publishing. Hidden from ``repr``: a remote
    URL may carry credentials."""
    git_branch: str = "main"
    writer_lock_timeout: float = 30.0
    """Seconds a writer waits for the process-wide and cross-process writer lock."""


class ConfigurationError(Exception):
    """A safe diagnostic that can be printed without disclosing configuration values."""


_CONFIG_KEYS = frozenset({"content_dir", "state_dir", "key_file", "git_name", "git_email", "git_remote", "git_branch"})
_LIMIT_KEYS = frozenset({"proposals_per_hour", "proposals_pending", "writes_per_hour"})
_MAX_CONFIG_BYTES = 65536


def _read_config(path: Path) -> dict[str, str]:
    try:
        with open(path, "rb", opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK)) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ConfigurationError("configuration must be a regular TOML file")
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ConfigurationError("configuration exceeds the byte limit")
        data = tomllib.loads(raw.decode("utf-8"))
    except OSError, UnicodeError, tomllib.TOMLDecodeError, RecursionError, ValueError:
        raise ConfigurationError("cannot read configuration as UTF-8 TOML") from None
    if data.keys() - (_CONFIG_KEYS | _LIMIT_KEYS):
        raise ConfigurationError("configuration contains an unknown key or invalid value")
    for key, entry in data.items():
        if key in _LIMIT_KEYS:
            if type(entry) is not int or entry <= 0:
                raise ConfigurationError("quota settings must be positive integers")
            try:
                data[key] = str(entry)
            except ValueError:
                raise ConfigurationError("quota setting exceeds integer size") from None
        elif type(entry) is not str:
            raise ConfigurationError("configuration contains an invalid value")
    return data


def load_settings(file: Path | None, environment: Mapping[str, str]) -> Settings:
    """File paths in TOML are relative to the file. Environment paths are relative to the working directory."""
    config = _read_config(file) if file is not None else {}

    def value(key: str, default: str | None = None) -> str:
        name = "CEREBRO_" + key.upper()
        text = environment.get(name, config.get(key, default))
        if text is None or not text.strip():
            raise ConfigurationError(f"missing configuration for {name}")
        if any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ConfigurationError(f"invalid configuration for {name}")
        return text

    def path(key: str) -> Path:
        setting = Path(value(key))
        try:
            if file is not None and "CEREBRO_" + key.upper() not in environment:
                return file.absolute().parent / setting
            return setting.absolute()
        except OSError:
            raise ConfigurationError("cannot resolve configuration paths") from None

    def limit(key: str, default: int) -> int:
        text = value(key, str(default))
        if not (text.isascii() and text.isdecimal()):
            raise ConfigurationError("quota settings must be positive integers")
        try:
            number = int(text)
        except ValueError:
            raise ConfigurationError("quota setting exceeds integer size") from None
        if number <= 0:
            raise ConfigurationError("quota settings must be positive integers")
        return number

    defaults = ProposalLimits()
    configuration = Settings(
        writes_per_hour=limit("writes_per_hour", 20),
        proposal_limits=ProposalLimits(
            per_hour=limit("proposals_per_hour", defaults.per_hour),
            pending=limit("proposals_pending", defaults.pending),
        ),
        content_repo=path("content_dir"),
        state_dir=path("state_dir"),
        key_file=path("key_file"),
        git_name=value("git_name"),
        git_email=value("git_email"),
        git_remote=value("git_remote") if "git_remote" in config or "CEREBRO_GIT_REMOTE" in environment else None,
        git_branch=value("git_branch", "main"),
    )
    try:
        GitIdentity(name=configuration.git_name, email=configuration.git_email)
        validate_branch(configuration.git_branch)
        if configuration.git_remote is not None:
            validate_remote(configuration.git_remote)
    except GitError:
        raise ConfigurationError("invalid git configuration") from None
    return configuration
