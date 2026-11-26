"""Capture dirty checkout entries, save them under state and restore HEAD without replacing later editor saves."""

import contextlib
import ctypes
import errno
import os
import shutil
import stat
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cerebro.adapters.outbound.filesystem.errors import NoteWriteError
from cerebro.adapters.outbound.filesystem.lock import file_lock
from cerebro.adapters.outbound.filesystem.repository import LOCK_FILE
from cerebro.adapters.outbound.filesystem.safeio import open_directory
from cerebro.adapters.outbound.git import GitError, GitRepo

_OPERATIONS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "BISECT_START",
)
_SCRATCH = "cerebro-quarantine-in-progress"


def _move(source: Path, target: Path, name: str) -> bool:
    """Atomically capture or publish an entry (file, symlink or directory), without ever replacing another entry."""
    parts = Path(name).parts
    try:
        with open_directory(source, parts[:-1]) as src:
            leaf = os.fsencode(parts[-1])
            os.stat(leaf, dir_fd=src, follow_symlinks=False)
            with open_directory(target, parts[:-1], create=True) as dst:
                # Linux/WSL renameat2(..., RENAME_NOREPLACE); a save racing with recovery stays exactly where it landed.
                if ctypes.CDLL(None, use_errno=True).renameat2(src, leaf, dst, leaf, 1):
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error))
                os.fsync(src)
                os.fsync(dst)
                return True
    except FileNotFoundError:
        return False


def _sync(directory: Path) -> None:
    with open_directory(directory, ()) as fd:
        os.fsync(fd)


def _visit(parent: int, leaf: str, relative: str, archive: tarfile.TarFile | None) -> None:
    """Preflight entries, or archive them from checked descriptors. Never dereference a captured symlink."""
    info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    if leaf == ".git":
        raise NoteWriteError("cannot quarantine a nested content repository")
    entry = tarfile.TarInfo(relative)
    entry.mode = stat.S_IMODE(info.st_mode)
    entry.mtime = info.st_mtime
    entry.uid, entry.gid = info.st_uid, info.st_gid
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if stat.S_ISDIR(info.st_mode):
        directory = os.open(leaf, flags | os.O_DIRECTORY, dir_fd=parent)
        try:
            if not os.path.samestat(os.fstat(directory), info):
                raise NoteWriteError("a directory changed during quarantine")
            if archive is not None:
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            for child in sorted(os.listdir(directory)):
                _visit(directory, child, f"{relative}/{child}", archive)
        finally:
            os.close(directory)
    elif stat.S_ISLNK(info.st_mode):
        if archive is not None:
            entry.type = tarfile.SYMTYPE
            entry.linkname = os.readlink(leaf, dir_fd=parent)
            archive.addfile(entry)
    elif stat.S_ISREG(info.st_mode):
        if archive is not None:
            with os.fdopen(os.open(leaf, flags, dir_fd=parent), "rb") as handle:
                if not os.path.samestat(os.fstat(handle.fileno()), info):
                    raise NoteWriteError("a file changed during quarantine")
                entry.size = info.st_size
                archive.addfile(entry, handle)
    else:
        raise NoteWriteError("cannot quarantine a special file")
    if os.stat(leaf, dir_fd=parent, follow_symlinks=False).st_ctime_ns != info.st_ctime_ns:
        raise NoteWriteError("an entry changed during quarantine")


def _visit_root(root: Path, name: str, archive: tarfile.TarFile | None) -> None:
    path = Path(name)
    with open_directory(root, path.parts[:-1]) as parent:
        _visit(parent, path.name, name, archive)


def _restore_entries(source: Path, root: Path, names: list[str], pending: Path) -> bool:
    blocked = []
    for name in names:
        try:
            if not _move(source, root, name) and os.path.lexists(root / name):
                blocked.append(name)
        except OSError:
            blocked.append(name)
    if blocked:
        (pending / "unrestored-paths").write_bytes(b"".join(os.fsencode(name) + b"\0" for name in blocked))
    return not blocked


def _retain(captured: Path, pending: Path, dot_git: Path) -> Path:
    # A pre-existing editor descriptor can write to a captured inode even after its tar snapshot closes.
    try:
        return captured.rename(pending / "originals")
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        retained = captured.rename(dot_git / pending.with_suffix("").name)
        _sync(dot_git)
        return retained


def _recover(root: Path, state: Path, git: GitRepo, head: str, status: str) -> None:
    entries = [entry for entry in status.split("\0") if entry]
    if any("U" in e[:2] or e[:2] in ("AA", "DD") for e in entries):
        raise NoteWriteError("resolve the content repository merge before quarantining changes")
    names = {e[3:] for e in entries}
    if any(
        not name or name.startswith("/") or name.endswith("/") or {".git", ".."} & set(Path(name).parts)
        for name in names
    ):
        raise NoteWriteError("cannot quarantine an unsupported content path")
    # If a file replaced a directory (or the reverse), capture the entire replacement exactly once.
    roots = sorted(name for name in names if not any(str(parent) in names for parent in Path(name).parents))
    for name in roots:
        with contextlib.suppress(
            FileNotFoundError
        ):  # A deleted path has only a status entry and possibly staged bytes.
            _visit_root(root, name, None)
    indexed = sorted({e[3:] for e in entries if e[:2] not in ("??", "!!")})
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    pending = Path(tempfile.mkdtemp(prefix=f"quarantine-{stamp}-", suffix=".partial", dir=state))
    complete = pending.with_suffix("")
    (pending / "status").write_bytes(head.encode() + b"\0" + os.fsencode(status))
    git.save_staged_patch(pending / "staged.patch")
    # Same filesystem as the content checkout even when state lives on another mounted volume.
    scratch = root / ".git" / _SCRATCH
    scratch.mkdir(mode=0o700)
    captured, restored = scratch / "captured", scratch / "restored"
    captured.mkdir(mode=0o700)
    restored.mkdir()
    moved: list[str] = []
    archived = False
    try:
        git.prepare_quarantine_restore(indexed, head, restored, scratch / "index")
        for name in roots:
            moved.append(name)
            if not _move(root, captured, name):
                moved.pop()
        # Archive the entries actually removed, including a version saved between status and the atomic move.
        with tarfile.open(pending / "worktree.tar", "w") as archive:
            for name in moved:
                _visit_root(captured, name, archive)
        for path in pending.iterdir():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        captured = _retain(captured, pending, root / ".git")
        _sync(pending)
        _sync(state)
        archived = True
        if git.head() != head:
            raise NoteWriteError("the content HEAD changed during quarantine")
        restored_all = _restore_entries(restored, root, roots, pending)
        # Each directory is visited once, after all files are back in place, deepest first.
        parents = {p for name in roots for p in Path(name).parents if len(p.parts) > 1}
        for path in sorted(parents, key=lambda p: len(p.parts), reverse=True):
            try:
                with open_directory(root, path.parts[:-1]) as parent:
                    os.rmdir(path.name, dir_fd=parent)
            except OSError as error:
                if error.errno not in (errno.ENOTEMPTY, errno.ENOENT, errno.ENOTDIR):
                    raise
        os.replace(scratch / "index", root / ".git/index")
        _sync(root / ".git")
        if not restored_all:
            raise NoteWriteError("some checkout paths could not be restored; see unrestored-paths in quarantine")
        pending.rename(complete)
        _sync(state)
    except BaseException:
        if not archived and not _restore_entries(captured, root, moved, pending) and captured.parent == scratch:
            _retain(captured, pending, root / ".git")
        raise
    finally:
        # Leave raw originals in place if even moving them to private storage failed.
        if not any(os.path.lexists(scratch / "captured" / name) for name in moved):
            shutil.rmtree(scratch)


def quarantine_changes(root: Path, state: Path, git: GitRepo, timeout: float) -> None:
    if state.resolve().is_relative_to(root.resolve()):
        raise NoteWriteError("quarantine requires a state directory outside the content repository")
    dot_git = root / ".git"
    with file_lock(dot_git / LOCK_FILE, exclusive=True, timeout=timeout):
        if any(os.path.lexists(dot_git / marker) for marker in (*_OPERATIONS, _SCRATCH)):
            raise NoteWriteError("inspect the unfinished git operation or .git/cerebro-quarantine-in-progress")
        try:
            if not git.working_changes():
                return
            lock = dot_git / "index.lock"
            with lock.open("xb"):
                try:
                    status = git.working_changes()
                    if status:
                        head = git.head()
                        if not head or any(os.path.lexists(dot_git / marker) for marker in _OPERATIONS):
                            raise NoteWriteError("quarantine requires a committed HEAD and no active git operation")
                        _recover(root, state, git, head, status)
                finally:
                    lock.unlink()
        except GitError, OSError, tarfile.TarError:
            raise NoteWriteError(
                "could not quarantine and restore content; inspect state and the content checkout"
            ) from None
