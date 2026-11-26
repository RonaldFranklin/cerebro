"""Git adapters: a hardened wrapper around the ``git`` CLI and the ``VersionControl`` port on top of it.

Hardening (every call, ``init`` included):

* never a shell: the command is an argument list, ``--`` precedes every path, paths are validated first (relative,
  no ``.``/``..``, no ``.git`` component, no symlink in any component, no directory, no ``.gitattributes`` /
  ``.gitmodules`` / ``.gitignore``);
* a fixed, minimal environment: nothing inherited, system and global config disabled, ``HOME`` is an empty private
  temp directory, ``LC_ALL=C``, no terminal prompts, literal pathspecs. ``passthrough_env`` may copy only an
  allowlist of variables (ssh agent, ssh command, TLS bundle, proxies); anything else is refused;
* command-line overrides, which win over the repository's own ``.git/config``: hooks, signing, fsmonitor, pager,
  askpass, credential helpers, signature display, global attributes and submodule recursion off; ``core.sshCommand``
  is plain ``ssh`` (a ``GIT_SSH_COMMAND`` passed through still takes precedence, by git's own rules); every transport
  is denied except https, ssh and file (``ext::`` and friends never run); the identity comes from the constructor;
* ``--git-dir`` / ``--work-tree`` are explicit, so git never walks up to a parent repository;
* ``safe.directory`` is passed for the repository root on the command line (the only place git honours it besides
  system/global config, which are disabled): the root is operator configuration, not user input;
* the git executable is resolved to an absolute path and only absolute directories reach the child ``PATH``;
* a timeout on every call (the whole process group is killed on any exit path), stdout read through a pipe with a
  hard cap (exceeding it kills git and raises, so data is never silently partial), the commit message through
  stdin (``-F -``), never the command line;
* errors are ``GitError``: sub-command, exit code and a heavily scrubbed hint (quoted strings, URLs, anything that
  looks like a path, the configured remote and the repository root are removed). Never the commit message.

Rollback: ``discard_changes`` restores only the ``modified`` paths it is given and deletes only ``created`` paths
that did not exist before the transaction (the caller records them with ``snapshot_existing`` first). Out-of-band
edits to other files, drafts and ignored files are left untouched on purpose. It is NOT atomic across its git calls:
when it raises, the error is surfaced and the caller must check ``is_clean()`` / ``head()`` before going on.

Residual risks (config-driven, not overridable from the command line): the repository's ``.git/config`` can still
define ``filter.*`` drivers (run through ``.gitattributes``), ``diff``/``merge`` drivers and ``url.*.insteadOf``
rewrites and ``remote.<name>.receivepack`` / ``uploadpack`` (a configured remote name can name a command to run;
a push by URL does not read them). ssh itself reads the real ``~/.ssh/config`` of the service user (``HOME`` only
hides git's own config), so keep that file trustworthy. The content repository is the owner's own private
repository; do not point Cérebro at one you do not trust. Refusing to stage ``.gitattributes`` narrows how a
note write could activate such a driver.
"""

import contextlib
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import weakref
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_PUSH_TIMEOUT: Final = 120.0
MAX_OUTPUT_BYTES: Final = 1024 * 1024
MAX_STDIN_BYTES: Final = 32 * 1024  # below the pipe buffer: writing it can never block
_MAX_STDERR_BYTES: Final = 16 * 1024
_MAX_ERROR_CHARS: Final = 200
_BRANCH: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
_URL: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S*")
_QUOTED: Final = re.compile(r"'[^']*'|\"[^\"]*\"")
_PATHLIKE_WORD: Final = re.compile(r"\S*[/@=?\\]\S*")
_SAFE_PATH: Final = ("/usr/local/bin", "/usr/bin", "/bin")
_FORBIDDEN_NAMES: Final = frozenset({".gitattributes", ".gitmodules", ".gitignore"})
ALLOWED_PASSTHROUGH_ENV: Final = frozenset(
    {
        "SSH_AUTH_SOCK",
        "GIT_SSH_COMMAND",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
)


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
    """Reduce git's stderr to a harmless hint: no URLs (userinfo, query), quoted strings, paths or secrets."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    if root is not None:
        text = text.replace(str(root), "<repo>")
    text = _URL.sub("<url>", text)
    text = _QUOTED.sub("<quoted>", text)
    text = _PATHLIKE_WORD.sub("<redacted>", text)
    return " ".join(text.split())[:_MAX_ERROR_CHARS]


def validate_branch(branch: str) -> str:
    if _BRANCH.fullmatch(branch) is None or ".." in branch or branch.endswith((".lock", "/", ".")):
        raise GitError("invalid branch name")
    return branch


def validate_remote(remote: str) -> str:
    """A configured remote name or URL: not an option, no whitespace or control characters."""
    if not remote or remote.startswith("-") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in remote):
        raise GitError("invalid remote")
    return remote


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
        """``passthrough_env`` names environment variables copied to every call, from ``ALLOWED_PASSTHROUGH_ENV``
        only (for example ``SSH_AUTH_SOCK`` when pushing over ssh). Empty by default: nothing is inherited."""
        if not root.is_dir():
            raise GitError("repository directory does not exist")
        refused = [name for name in passthrough_env if name not in ALLOWED_PASSTHROUGH_ENV]
        if refused:
            raise GitError(f"environment variable not allowed: {refused[0].split('=')[0][:40]}")
        found = shutil.which(git_executable)
        if found is None:
            raise GitError("git executable not found")
        git_path = Path(found).resolve()
        if not git_path.is_absolute():  # pragma: no cover - resolve() always returns an absolute path
            raise GitError("git executable must resolve to an absolute path")
        self._root = root.resolve()
        if (self._root / ".git").is_symlink():
            raise GitError("the repository's .git must not be a symlink")
        self._identity = identity
        self._timeout = timeout
        self._git = str(git_path)
        self._home = tempfile.mkdtemp(prefix="cerebro-git-home-")
        weakref.finalize(self, _remove_dir, self._home)
        self._env = self._build_env(passthrough_env)

    # -- environment ---------------------------------------------------------------------------------------------

    def _build_env(self, passthrough: Sequence[str]) -> dict[str, str]:
        env = {
            "PATH": os.pathsep.join([str(Path(self._git).parent), *_SAFE_PATH]),
            "HOME": self._home,
            "XDG_CONFIG_HOME": self._home,
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LITERAL_PATHSPECS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        for name in passthrough:
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def _base_args(self, *, with_dirs: bool = True) -> list[str]:
        overrides = [
            f"core.hooksPath={os.devnull}",
            f"core.attributesFile={os.devnull}",
            "core.fsmonitor=false",
            "core.pager=cat",
            "core.askPass=",
            "core.sshCommand=ssh",
            "commit.gpgsign=false",
            "tag.gpgsign=false",
            "log.showSignature=false",
            "credential.helper=",
            "submodule.recurse=false",
            "gc.auto=0",
            "protocol.allow=never",
            "protocol.https.allow=always",
            "protocol.ssh.allow=always",
            "protocol.file.allow=always",
            "protocol.ext.allow=never",
            f"safe.directory={self._root}",
            f"user.name={self._identity.name}",
            f"user.email={self._identity.email}",
        ]
        args = [self._git]
        if with_dirs:
            args += [f"--git-dir={self._root / '.git'}", f"--work-tree={self._root}"]
        for item in overrides:
            args += ["-c", item]
        return args

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
        if not init:
            git_dir = self._root / ".git"
            if git_dir.is_symlink() or not git_dir.is_dir():
                raise GitError("the repository's .git must be a real directory")
        command = [*self._base_args(with_dirs=not init), *args]
        data_in = stdin.encode() if stdin is not None else b""
        if len(data_in) > MAX_STDIN_BYTES:
            raise GitError(f"git {args[0]}: input too large")
        limit = self._timeout if timeout is None else timeout
        try:
            process = subprocess.Popen(  # argument list, never a shell
                command,
                cwd=self._root,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise GitError(f"git {args[0]}: could not start git") from None
        try:
            out, err = self._communicate(process, data_in, limit, args[0])
        finally:
            self._kill_group(process)
        if process.returncode not in ok:
            detail = scrub(err.decode("utf-8", errors="replace"), secrets=redact, root=self._root)
            raise GitError(f"git {args[0]} failed (exit {process.returncode}): {detail}")
        return process.returncode, out.decode("utf-8", errors="surrogateescape")

    @staticmethod
    def _communicate(process: subprocess.Popen[bytes], data_in: bytes, limit: float, name: str) -> tuple[bytes, bytes]:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        deadline = time.monotonic() + limit
        try:
            if data_in:
                process.stdin.write(data_in)
            process.stdin.close()
        except BrokenPipeError:
            pass  # git exited early; its exit code and stderr tell why
        buffers: dict[int, bytearray] = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
        stdout_fd = process.stdout.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GitError(f"git {name}: timed out")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = buffers[key.fd]
                    if key.fd == stdout_fd:
                        buffer += chunk
                        if len(buffer) > MAX_OUTPUT_BYTES:
                            raise GitError(f"git {name}: output exceeds the size limit")
                    elif len(buffer) < _MAX_STDERR_BYTES:
                        buffer += chunk[: _MAX_STDERR_BYTES - len(buffer)]
        try:
            process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            raise GitError(f"git {name}: timed out") from None
        return bytes(buffers[stdout_fd]), bytes(buffers[process.stderr.fileno()])

    @staticmethod
    def _kill_group(process: subprocess.Popen[bytes]) -> None:
        """Always runs: no descendant of git (ssh, helpers) survives the call."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    # -- path validation -----------------------------------------------------------------------------------------

    def _check_path(self, path: str | PurePath, *, allow_final_symlink: bool = False) -> str:
        """A repository-relative path of one regular file (or, for removal, a symlink entry); never a directory."""
        text = str(path)
        if not text or "\x00" in text or text.startswith("-"):
            raise GitError("invalid path")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise GitError("path must be valid UTF-8") from None
        pure = PurePath(text)
        if pure.is_absolute() or pure.drive or not pure.parts or pure.parts == (".",):
            raise GitError("path must be a relative file path")
        if ".." in pure.parts or "." in pure.parts or any(p.lower() == ".git" for p in pure.parts):
            raise GitError("path must stay inside the repository")
        if pure.name.lower() in _FORBIDDEN_NAMES:
            raise GitError("git control files are not managed here")
        current = self._root
        for index, part in enumerate(pure.parts):
            current = current / part
            if current.is_symlink() and not (allow_final_symlink and index == len(pure.parts) - 1):
                raise GitError("symlinks are not allowed in paths")
        resolved = current.parent.resolve() / current.name  # a final symlink entry is judged by its own location
        if not resolved.is_relative_to(self._root):
            raise GitError("path must stay inside the repository")
        if any(p.lower() == ".git" for p in resolved.relative_to(self._root).parts):
            raise GitError("path must stay inside the repository")
        if current.is_dir() and not current.is_symlink():
            raise GitError("path must name a file, not a directory")
        return pure.as_posix()

    def _check_paths(self, paths: Iterable[str | PurePath], *, allow_final_symlink: bool = False) -> list[str]:
        return [self._check_path(p, allow_final_symlink=allow_final_symlink) for p in paths]

    @staticmethod
    def _names(paths: Iterable[str]) -> list[str]:
        """Paths and their base names, to be removed from error hints."""
        found = list(paths)
        return [*found, *(PurePath(p).name for p in found)]

    # -- operations ----------------------------------------------------------------------------------------------

    def init(self, initial_branch: str) -> None:
        """Create the repository (a no-op for an existing one), with the same hardening as every other call."""
        validate_branch(initial_branch)
        self._run(["init", "--quiet", f"--initial-branch={initial_branch}"], init=True)

    def head(self) -> str | None:
        """The id of the current commit, or ``None`` when the repository has no commits yet."""
        code, out = self._run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], ok=(0, 1))
        return out.strip() if code == 0 else None

    def stage(self, paths: Iterable[str | PurePath]) -> None:
        """Stage additions, modifications and deletions of the listed files (literal paths, no globs)."""
        checked = self._check_paths(paths)
        if checked:
            self._run(["add", "--all", "--", *checked], redact=self._names(checked))

    def commit(self, message: str, paths: Iterable[str | PurePath] | None = None) -> str:
        """Commit with ``message`` (kept verbatim) as the configured identity; returns the new commit id.

        With ``paths``, ``git commit --only -- <paths>`` records exactly those (already staged or tracked) files and
        nothing else, whatever else sits in the index. Without, the whole index is committed.
        Hooks and signing are off. Raises ``GitError`` when there is nothing to commit.
        """
        if not message.strip():
            raise GitError("empty commit message")
        text = message if message.endswith("\n") else message + "\n"
        args = ["commit", "--no-verify", "--no-gpg-sign", "--cleanup=verbatim", "--quiet", "-F", "-"]
        redact: list[str] = []
        if paths is not None:
            checked = self._check_paths(paths)
            if not checked:
                raise GitError("no paths to commit")
            args += ["--only", "--", *checked]
            redact = self._names(checked)
        self._run(args, stdin=text, redact=redact)
        commit_id = self.head()
        if commit_id is None:  # pragma: no cover - a successful commit always creates HEAD
            raise GitError("commit left no HEAD")
        return commit_id

    def recent_revisions(self, limit: int) -> tuple[str, ...]:
        if not 1 <= limit <= 64:
            raise GitError("invalid history window")
        if not self.head():
            return ()
        _, output = self._run(["rev-list", "--first-parent", f"--max-count={limit}", "HEAD", "--"])
        revisions = tuple(output.splitlines())
        for revision in revisions:
            self._require_object_id(revision)
        return revisions

    @staticmethod
    def _require_object_id(revision: str) -> None:
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            raise GitError("invalid object id")

    def history_commit(self, revision: str) -> tuple[str, str, str, str] | None:
        """Parent, committer time, message and affected path for a bounded, single-file commit."""
        self._require_object_id(revision)
        _, size = self._run(["cat-file", "-s", revision])
        if int(size) > 8192:
            return None
        _, raw = self._run(["show", "-s", "--no-notes", "--no-patch", "--format=%P%x00%cI%x00%B", revision, "--"])
        parents, timestamp, message = raw.split("\x00", 2)
        if parents:
            if " " in parents:
                return None
            self._require_object_id(parents)
        _, changes = self._run(
            [
                "diff-tree",
                "--root",
                "-r",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--no-commit-id",
                "--name-only",
                "-z",
                revision,
                "--",
            ]
        )
        paths = changes.rstrip("\x00").split("\x00")
        if len(paths) != 1:
            return None
        return parents, timestamp, message.rstrip("\n"), paths[0]

    def historical_blob(self, revision: str, paths: Sequence[str], limit: int) -> tuple[str, bytes] | None:
        self._require_object_id(revision)
        if not paths or any(not re.fullmatch(r"(?:core|notes|hubs)/[a-z0-9-]{1,80}\.md", path) for path in paths):
            raise GitError("invalid historical note path")
        _, tree = self._run(["ls-tree", "-l", "-z", revision, "--", *paths])
        if not tree:
            return None
        entries = tree.rstrip("\x00").split("\x00")
        if len(entries) != 1:
            raise GitError("ambiguous historical note")
        header, _, path = entries[0].partition("\t")
        mode, kind, oid, size = header.split()
        if mode not in ("100644", "100755") or kind != "blob" or path not in paths or int(size) > limit:
            raise GitError("unreadable historical note")
        self._require_object_id(oid)
        _, content = self._run(["cat-file", "blob", oid])
        data = content.encode("utf-8", errors="surrogateescape")
        if len(data) != int(size):
            raise GitError("historical blob size mismatch")
        return path, data

    def describe_commit(self, revision: str) -> tuple[tuple[str, ...], str]:
        """``(parent ids, message)`` of a commit; the message without its trailing newline."""
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise GitError("invalid revision")
        _, out = self._run(["log", "-1", "--format=%P%x00%B", revision, "--"])
        parents, _, text = out.partition("\x00")
        return tuple(parents.split()), text.strip("\n")

    def find_create_commit(self, request_key: str) -> tuple[str, str] | None:
        """Look for an exact create-request trailer across the reachable content history."""
        if not re.fullmatch(r"[0-9a-f]{64}", request_key):
            raise GitError("invalid request key")
        if self.head() is None:
            return None
        _, out = self._run(
            ["log", "-1", "--basic-regexp", f"--grep=^Write-Request: {request_key}$", "--format=%H%x00%B", "HEAD", "--"]
        )
        if not out:
            return None
        revision, _, message = out.partition("\x00")
        return revision, message.rstrip("\n")

    def staged_paths(self) -> tuple[str, ...]:
        """Paths whose index entry differs from ``HEAD`` (before the first commit: every path in the index)."""
        if self.head() is None:
            _, listing = self._run(["ls-files", "-z"])
        else:
            _, listing = self._run(["diff", "--cached", "--name-only", "--no-renames", "-z"])
        return tuple(sorted(name for name in listing.split("\x00") if name))

    def uncommitted_paths(self, pathspecs: Sequence[str]) -> tuple[str, ...]:
        """Paths under ``pathspecs`` that differ from ``HEAD`` in the index or the working tree: staged, modified,
        deleted, untracked and ignored files alike. A pathspec is a top-level note folder or one note file in it."""
        if not pathspecs or any(
            not re.fullmatch(r"(?:core|hubs|notes)(?:/[a-z0-9-]{1,80}\.md)?", p) for p in pathspecs
        ):
            raise GitError("invalid note pathspec")
        _, out = self._run(
            [
                "status",
                "--porcelain=v1",
                "-z",
                "--no-renames",
                "--untracked-files=all",
                "--ignored=matching",
                "--",
                *pathspecs,
            ]
        )
        return tuple(sorted({entry[3:] for entry in out.split("\x00") if len(entry) > 3}))

    def _head_entry(self, path: str | PurePath) -> str:
        """Check the committed tree independently of changes staged or edited by the owner."""
        checked = self._check_path(path, allow_final_symlink=True)
        if self.head() is None:
            return ""
        _, listing = self._run(["ls-tree", "-z", "HEAD", "--", checked], redact=self._names([checked]))
        return listing

    def regular_file_in_head(self, path: str | PurePath) -> bool:
        """True when HEAD contains a regular file at this path, including executable files."""
        return self._head_entry(path).startswith(("100644 blob ", "100755 blob "))

    def matches_head(self, path: str | PurePath) -> bool:
        """True when ``path`` is tracked in ``HEAD`` and its working-tree bytes equal the committed ones."""
        if not self._head_entry(path):
            return False
        checked = self._check_path(path, allow_final_symlink=True)
        code, _ = self._run(["diff", "--quiet", "HEAD", "--", checked], ok=(0, 1), redact=self._names([checked]))
        return code == 0

    def snapshot_existing(self, paths: Iterable[str | PurePath]) -> frozenset[str]:
        """Which of ``paths`` already exist (on disk, as a symlink, or in the index) right now.

        Call it before writing; hand the result to ``discard_changes`` so that a path a failed transaction merely
        overwrote or staged, but did not create, is never deleted.
        """
        checked = self._check_paths(paths, allow_final_symlink=True)
        existing = {p for p in checked if os.path.lexists(self._root / p)}
        if checked:
            _, listing = self._run(["ls-files", "-z", "--", *checked], redact=self._names(checked))
            existing.update(name for name in listing.split("\x00") if name)
        return frozenset(existing)

    def discard_changes(
        self,
        *,
        created: Iterable[str | PurePath],
        modified: Iterable[str | PurePath],
        preexisting: frozenset[str] = frozenset(),
    ) -> None:
        """Roll back an apply, touching only the listed files.

        ``modified`` files (tracked in ``HEAD``, possibly deleted on disk) are restored in index and working tree.
        ``created`` files are unstaged and deleted, unless they are in ``preexisting`` (the ``snapshot_existing``
        taken before the apply): those were not made by the transaction and are left exactly as they are. Paths
        that are trees in ``HEAD`` and directories are refused. Other files are never touched. Empty parent
        directories are left behind. Not atomic: see the module docstring.
        """
        to_restore = self._check_paths(modified, allow_final_symlink=True)
        to_remove = [p for p in self._check_paths(created, allow_final_symlink=True) if p not in preexisting]
        every = [*to_restore, *to_remove]
        if not every:
            return
        redact = self._names(every)
        in_head: dict[str, str] = {}
        if self.head() is not None:
            _, listing = self._run(["ls-tree", "-z", "HEAD", "--", *every], redact=redact)
            for entry in filter(None, listing.split("\x00")):
                meta, _, name = entry.partition("\t")
                in_head[name] = meta.split()[1]
        if "tree" in in_head.values():
            raise GitError("a path names a directory in HEAD")
        if any(p not in in_head for p in to_restore):
            raise GitError("a modified path is not tracked in HEAD")
        if any(p in in_head for p in to_remove):
            raise GitError("a created path is tracked in HEAD")
        if to_restore:
            self._run(["restore", "--source=HEAD", "--staged", "--worktree", "--", *to_restore], redact=redact)
        if to_remove:
            self._run(["rm", "--cached", "-f", "--quiet", "--ignore-unmatch", "--", *to_remove], redact=redact)
            self._run(["clean", "-f", "--quiet", "--", *to_remove], redact=redact)

    def is_clean(self) -> bool:
        """True when there are no staged, unstaged or untracked changes."""
        _, out = self._run(["status", "--porcelain", "--untracked-files=all"])
        return not out.strip()

    def push(self, remote: str, branch: str, *, timeout: float = DEFAULT_PUSH_TIMEOUT) -> None:
        """``git push -- <remote> <branch>``. ``remote`` is a configured name or URL; it is redacted from errors."""
        validate_branch(branch)
        validate_remote(remote)
        self._run(["push", "--quiet", "--", remote, branch], timeout=timeout, redact=[remote])


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
        self._remote = validate_remote(remote) if remote else None
        self._branch = validate_branch(branch)
        self._push_timeout = push_timeout

    def push(self) -> None:
        """Push ``branch`` to ``remote``; a no-op when no remote is configured. Raises ``GitError`` on failure."""
        if self._remote is None:
            return
        self._repo.push(self._remote, self._branch, timeout=self._push_timeout)


__all__ = [
    "ALLOWED_PASSTHROUGH_ENV",
    "GitError",
    "GitIdentity",
    "GitRepo",
    "GitVersionControl",
    "scrub",
    "validate_branch",
    "validate_remote",
]
