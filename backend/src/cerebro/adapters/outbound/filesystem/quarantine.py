"""Preserve both versions of a dirty content checkout outside Git before restoring the last commit."""

import errno
import os
import stat
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cerebro.adapters.outbound.filesystem.errors import NoteWriteError
from cerebro.adapters.outbound.filesystem.lock import file_lock
from cerebro.adapters.outbound.filesystem.repository import LOCK_FILE
from cerebro.adapters.outbound.filesystem.safeio import open_directory, remove_file
from cerebro.adapters.outbound.git import GitError, GitRepo


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_ctime_ns


def _archive_entry(archive: tarfile.TarFile, root: Path, name: str) -> tuple[int, int, int] | None:
    parts = Path(name).parts
    # All names originate in git porcelain. Refuse a submodule or any non-file entry before restoring anything.
    if not parts or name.startswith("/") or ".." in parts or ".git" in parts or name.endswith("/"):
        raise NoteWriteError("cannot quarantine an unsupported content path")
    try:
        with open_directory(root, parts[:-1]) as directory:
            info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            entry = tarfile.TarInfo(name)
            entry.mode = stat.S_IMODE(info.st_mode)
            entry.mtime = info.st_mtime
            if stat.S_ISLNK(info.st_mode):
                entry.type = tarfile.SYMTYPE
                entry.linkname = os.readlink(parts[-1], dir_fd=directory)
                archive.addfile(entry)
            elif stat.S_ISREG(info.st_mode):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                with os.fdopen(os.open(parts[-1], flags, dir_fd=directory), "rb") as source:
                    if _identity(os.fstat(source.fileno())) != _identity(info):
                        raise NoteWriteError("content changed during quarantine")
                    entry.size = info.st_size
                    archive.addfile(entry, source)
            else:
                raise NoteWriteError("cannot quarantine a non-file content entry")
            return _identity(info)
    except FileNotFoundError:
        return None  # Deleted paths are recorded in status, and staged bytes in staged.patch.


def _current_identity(root: Path, name: str) -> tuple[int, int, int] | None:
    parts = Path(name).parts
    try:
        with open_directory(root, parts[:-1]) as directory:
            return _identity(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False))
    except FileNotFoundError:
        return None


def quarantine_changes(root: Path, state: Path, git: GitRepo, timeout: float) -> None:
    if state.resolve().is_relative_to(root.resolve()):
        raise NoteWriteError("quarantine requires a state directory outside the content repository")
    with file_lock(root / ".git" / LOCK_FILE, exclusive=True, timeout=timeout):
        try:
            status = git.working_changes()
            if not status:
                return
            head = git.head()
            if not head or os.path.lexists(root / ".git/index.lock"):
                raise NoteWriteError("quarantine requires a committed HEAD and an unlocked git index")
            index_before = _current_identity(root, ".git/index")
            entries = [entry for entry in status.split("\0") if entry]
            if any("U" in e[:2] or e[:2] in ("AA", "DD") for e in entries):
                raise NoteWriteError("resolve the content repository merge before quarantining changes")
            names = sorted({e[3:] for e in entries})
            indexed = sorted({e[3:] for e in entries if e[:2] not in ("??", "!!")})
            # A unique private directory, published only once every byte is durable. Failed copies are retained too.
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            pending = Path(tempfile.mkdtemp(prefix=f".quarantine-{stamp}-", dir=state))
            (pending / "status").write_bytes(head.encode() + b"\0" + os.fsencode(status))
            git.save_staged_patch(pending / "staged.patch")
            with tarfile.open(pending / "worktree.tar", "w") as archive:
                saved = {name: _archive_entry(archive, root, name) for name in names}
            for path in pending.iterdir():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            with open_directory(pending, ()) as directory:
                os.fsync(directory)
            pending.rename(state / pending.name[1:])
            with open_directory(state, ()) as directory:
                os.fsync(directory)
            if (
                git.head() != head
                or git.working_changes() != status
                or _current_identity(root, ".git/index") != index_before
                or any(_current_identity(root, name) != identity for name, identity in saved.items())
            ):
                raise NoteWriteError("content changed during quarantine")
            git.restore_quarantined(indexed, head)
            for name in set(names).difference(indexed):
                remove_file(root, name)
                # An empty nested directory in a flat note folder would still block writes.
                parts = Path(name).parts[:-1]
                while len(parts) > 1:
                    with open_directory(root, parts[:-1]) as parent:
                        try:
                            os.rmdir(parts[-1], dir_fd=parent)
                        except OSError as error:
                            if error.errno == errno.ENOTEMPTY:
                                break
                            raise
                    parts = parts[:-1]
        except GitError, OSError, tarfile.TarError:
            raise NoteWriteError(
                "could not quarantine and restore the content repository; inspect the state directory"
            ) from None
