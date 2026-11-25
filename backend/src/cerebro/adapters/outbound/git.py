"""Git adapters: a hardened wrapper around the ``git`` CLI and the ``VersionControl`` port on top of it.

Hardening (every call):

* never a shell: the command is an argument list, ``--`` precedes every path, paths are validated first
  (relative, no ``..``, no ``.git``, no symlink escape);
* a fixed, minimal environment: nothing inherited (no ``GIT_*`` variables, no ``HOME`` config), system and global
  config disabled, ``HOME`` is an empty private temp directory, ``LC_ALL=C``, no terminal prompts, literal pathspecs;
* command-line overrides that win over repository config: hooks off, signing off, fsmonitor off, ``ext::`` transport
  off, credential helpers off; the identity comes from the constructor, never from any config file;
* ``--git-dir`` / ``--work-tree`` are explicit, so git never walks up to a parent repository;
* ``safe.directory`` is passed for the repository root on the command line (the only place git honours it besides
  system/global config, which are disabled): the root is operator configuration, not user input;
* a timeout on every call (the whole process group is killed), stdout and stderr capped in size;
* the commit message goes through stdin (``-F -``), never the command line;
* errors are ``GitError`` and carry only the sub-command, exit code and a scrubbed, truncated stderr: no commit
  message, no URL credentials, no paths.

Residual risk: the repository's own ``.git/config`` is still read, so keys that cannot be overridden here (for
example ``core.sshCommand``) would run if someone with write access to the content repository planted them. The
content repository is the owner's own, private repository; do not point Cérebro at a repository you do not trust.
"""

import os
import re
import shutil
import signal
import subprocess
import tempfile
import weakref
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import IO, Final

DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_PUSH_TIMEOUT: Final = 120.0
MAX_OUTPUT_BYTES: Final = 1024 * 1024
_MAX_ERROR_CHARS: Final = 500
_BRANCH: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
_USERINFO: Final = re.compile(r"(?<=://)[^/@\s'\"]+@")
_ABSOLUTE_PATH: Final = re.compile(r"(?<![\w:/.])/[^\s'\":]+")
_SAFE_PATH: Final = "/usr/local/bin:/usr/bin:/bin"


class GitError(Exception):
    """A git operation failed or was refused. The text never contains a commit message, credentials or paths."""


@dataclass(frozen=True, slots=True)
class GitIdentity:
    """Author and committer of the commits made through ``GitRepo``."""

    name: str
    email: str

    def __post_init__(self) -> None:
        for field, value in (("name", self.name), ("email", self.email)):
            if not value.strip() or any(ord(c) < 32 or c in "<>\x7f" for c in value):
                raise GitError(f"invalid git identity {field}")


def scrub(text: str, *, secrets: Iterable[str] = (), root: Path | None = None) -> str:
    """Remove from ``text`` everything an error must not carry: given secrets, URL userinfo, absolute paths."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    if root is not None:
        text = text.replace(str(root), "<repo>")
    text = _USERINFO.sub("***@", text)
    text = _ABSOLUTE_PATH.sub("<path>", text)
    return text.strip()[:_MAX_ERROR_CHARS]


def _remove_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


class GitRepo:
    """A working directory managed through the ``git`` CLI. Not thread-safe by itself: callers serialize writers."""

    def __init__(
        self,
        root: Path,
        *,
        identity: GitIdentity,
        timeout: float = DEFAULT_TIMEOUT,
        git_executable: str = "git",
        passthrough_env: Sequence[str] = (),
    ) -> None:
        """``passthrough_env`` names environment variables copied to every call (for example ``SSH_AUTH_SOCK`` or
        ``GIT_SSH_COMMAND`` when pushing over ssh). Empty by default: nothing is inherited."""
        if not root.is_dir():
            raise GitError("repository directory does not exist")
        resolved = shutil.which(git_executable)
        if resolved is None:
            raise GitError("git executable not found")
        self._root = root.resolve()
        self._identity = identity
        self._timeout = timeout
        self._git = resolved
        self._home = tempfile.mkdtemp(prefix="cerebro-git-home-")
        weakref.finalize(self, _remove_dir, self._home)
        self._env = self._build_env(passthrough_env)

    # -- environment ---------------------------------------------------------------------------------------------

    def _build_env(self, passthrough: Sequence[str]) -> dict[str, str]:
        env = {
            "PATH": os.pathsep.join([str(Path(self._git).parent), _SAFE_PATH]),
            "HOME": self._home,
            "XDG_CONFIG_HOME": self._home,
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LITERAL_PATHSPECS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        for name in passthrough:
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def _base_args(self) -> list[str]:
        return [
            self._git,
            f"--git-dir={self._root / '.git'}",
            f"--work-tree={self._root}",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "credential.helper=",
            "-c",
            f"safe.directory={self._root}",
            "-c",
            f"user.name={self._identity.name}",
            "-c",
            f"user.email={self._identity.email}",
            "-c",
            "gc.auto=0",
        ]

    # -- process execution ---------------------------------------------------------------------------------------

    def _run(
        self,
        args: Sequence[str],
        *,
        stdin: str | None = None,
        timeout: float | None = None,
        ok: Sequence[int] = (0,),
        redact: Sequence[str] = (),
        init: bool = False,
    ) -> tuple[int, str]:
        command = [self._git, *args] if init else [*self._base_args(), *args]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self._root,
                    env=self._env,
                    stdin=subprocess.PIPE,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
            except OSError:
                raise GitError(f"git {args[0]}: could not start git") from None
            try:
                process.communicate(
                    input=stdin.encode() if stdin is not None else None,
                    timeout=self._timeout if timeout is None else timeout,
                )
            except subprocess.TimeoutExpired:
                self._kill(process)
                raise GitError(f"git {args[0]}: timed out") from None
            except BrokenPipeError:
                process.wait()
            stdout = self._read(out)
            stderr = self._read(err)
        if process.returncode not in ok:
            detail = scrub(stderr, secrets=redact, root=self._root)
            raise GitError(f"git {args[0]} failed (exit {process.returncode}): {detail}")
        return process.returncode, stdout

    @staticmethod
    def _kill(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
        process.wait()

    @staticmethod
    def _read(handle: IO[bytes]) -> str:
        handle.seek(0)
        data = handle.read(MAX_OUTPUT_BYTES + 1)
        return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

    # -- path validation -----------------------------------------------------------------------------------------

    def _check_path(self, path: str | PurePath) -> str:
        text = str(path)
        if not text or "\x00" in text or text.startswith("-"):
            raise GitError("invalid path")
        pure = PurePath(text)
        if pure.is_absolute() or pure.drive:
            raise GitError("path must be relative")
        parts = pure.parts
        if ".." in parts or any(p.lower() == ".git" for p in parts):
            raise GitError("path must stay inside the repository")
        if not (self._root / pure).resolve().is_relative_to(self._root):
            raise GitError("path must stay inside the repository")
        return pure.as_posix()

    def _check_paths(self, paths: Iterable[str | PurePath]) -> list[str]:
        return [self._check_path(p) for p in paths]

    # -- operations ----------------------------------------------------------------------------------------------

    def init(self, initial_branch: str) -> None:
        """Create the repository (a no-op for an existing one)."""
        self._check_branch(initial_branch)
        self._run(["init", "--quiet", f"--initial-branch={initial_branch}"], init=True)

    def head(self) -> str | None:
        """The id of the current commit, or ``None`` when the repository has no commits yet."""
        code, out = self._run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], ok=(0, 1))
        return out.strip() if code == 0 else None

    def stage(self, paths: Iterable[str | PurePath]) -> None:
        """Stage additions, modifications and deletions of ``paths`` (literal paths, no globs)."""
        checked = self._check_paths(paths)
        if checked:
            self._run(["add", "--all", "--", *checked])

    add = stage

    def commit(self, message: str) -> str:
        """Commit the index with ``message`` (kept verbatim) as the configured identity; returns the new commit id.

        Hooks and signing are off. Raises ``GitError`` when there is nothing to commit.
        """
        if not message.strip():
            raise GitError("empty commit message")
        text = message if message.endswith("\n") else message + "\n"
        self._run(["commit", "--no-verify", "--no-gpg-sign", "--cleanup=verbatim", "--quiet", "-F", "-"], stdin=text)
        commit_id = self.head()
        if commit_id is None:  # pragma: no cover - a successful commit always creates HEAD
            raise GitError("commit left no HEAD")
        return commit_id

    def discard_changes(self, untracked: Iterable[str | PurePath] = ()) -> None:
        """Make the working tree and the index identical to ``HEAD`` (all-or-nothing rollback).

        Files created since ``HEAD`` are removed when listed in ``untracked`` (tracked-but-new files are removed
        by the reset itself); untouched untracked files elsewhere are left alone.
        """
        checked = self._check_paths(untracked)
        if self.head() is None:
            self._run(["rm", "-r", "-f", "--cached", "--quiet", "--ignore-unmatch", "--", "."])
        else:
            self._run(["reset", "--hard", "--quiet", "HEAD"])
        if checked:
            self._run(["clean", "-f", "-d", "-x", "--quiet", "--", *checked])

    def log(self, count: int = 10) -> list[str]:
        """The messages of the last ``count`` commits, newest first, each without its trailing newline."""
        if count < 1:
            raise GitError("count must be positive")
        if self.head() is None:
            return []
        _, out = self._run(["log", f"--max-count={count}", "--format=%B%x00"])
        return [m.strip("\n") for m in out.split("\x00") if m.strip()]

    def is_clean(self) -> bool:
        """True when there are no staged, unstaged or untracked changes."""
        _, out = self._run(["status", "--porcelain", "--untracked-files=all"])
        return not out.strip()

    def push(self, remote: str, branch: str, *, timeout: float = DEFAULT_PUSH_TIMEOUT) -> None:
        """``git push -- <remote> <branch>``. ``remote`` is a configured name or URL; it is redacted from errors."""
        self._check_branch(branch)
        if not remote or remote.startswith("-") or any(c.isspace() or ord(c) < 32 for c in remote):
            raise GitError("invalid remote")
        self._run(["push", "--quiet", "--", remote, branch], timeout=timeout, redact=[remote])

    @staticmethod
    def _check_branch(branch: str) -> None:
        if _BRANCH.fullmatch(branch) is None or ".." in branch or branch.endswith((".lock", "/", ".")):
            raise GitError("invalid branch name")


class GitVersionControl:
    """``VersionControl`` over git: publishes the commits of the content repository to a backup remote."""

    def __init__(
        self,
        repo: GitRepo,
        *,
        remote: str | None,
        branch: str,
        push_timeout: float = DEFAULT_PUSH_TIMEOUT,
    ) -> None:
        self._repo = repo
        self._remote = remote or None
        self._branch = branch
        self._push_timeout = push_timeout

    def push(self) -> None:
        """Push ``branch`` to ``remote``; a no-op when no remote is configured. Raises ``GitError`` on failure."""
        if self._remote is None:
            return
        self._repo.push(self._remote, self._branch, timeout=self._push_timeout)


__all__ = ["GitError", "GitIdentity", "GitRepo", "GitVersionControl", "scrub"]
