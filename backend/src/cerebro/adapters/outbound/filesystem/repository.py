"""``NoteRepository`` over the files of the content repository, committed with git.

Layout (domain model section 1): ``core/`` holds the notes of type ``core``, ``hubs/`` those of type ``hub`` and
``notes/`` all the others, flat, one file ``<id>.md`` per note. The folder follows from the type, so a change that
would alter a note's type across those groups is refused (a note never moves; ``id`` and ``type`` are immutable).

Problem files are named by relative path with a fixed reason (never content): symlinks anywhere, files over the
size cap, malformed frontmatter, ``id`` different from the file stem, a note in the wrong folder, the same id in two
folders, and any file that is neither a plaintext nor a valid encrypted note. ``scan`` reports every one of them
(``FileProblem``), never dropping a file silently; ``list_notes`` raises ``NoteFileError`` naming them all.
A problem is *stable* when the file equals ``HEAD`` and the reason follows from its bytes or kind alone (not for a
decryption failure, which depends on the configured key, nor for I/O errors): such a file is left out of ``scan``,
every copy of a duplicated id is left out, and ``get`` returns ``None`` for it, so it reads as missing everywhere.
Non-``.md`` files and dotfiles are ignored.

Only committed note content is served: ``get`` refuses and ``scan`` reports as not stable (``NoteFileError``, reason
``UNCOMMITTED_REASON``, naming every such path) a note file that differs from ``HEAD`` in the index or the working tree
(staged, edited, deleted, untracked or ignored), so an index rebuilt from ``list_notes`` and recorded as built from
``head()`` never holds uncommitted content (an abandoned edit lowering a sensitivity, an orphan left by an interrupted
create, a note left staged by a crash). Nothing is discarded automatically: the owner commits or restores the file.
Other files of the repository (``schema/``, ``README``) are not checked by reads.

Restricted notes (ADR-0008) are stored with ``encode_restricted``: a three-key plaintext header and a Fernet token of
the whole note file. On read the header id, the file stem and the inner ``id`` must agree and the inner sensitivity
must be ``restricted``. A plaintext file that declares ``sensitivity: restricted`` is **refused** (someone replaced
the encrypted file with a readable one). Changing a note between restricted and another sensitivity rewrites the same
path in the same commit, so the working tree keeps no leftover; the previous version stays in git history (plaintext
if it was public/internal, ciphertext if it was restricted): history is not rewritten.

``apply`` is all-or-nothing and makes exactly one commit: everything is validated first (``ContentConflict`` for a
stale ``expected_hash``, an existing note on create or a missing one on update), then, under an exclusive lock
(``.git/cerebro-notes.lock``, outside the tracked tree), the files are written atomically (temp file, fsync, rename,
fsync of the directory), staged and committed with the rendered commit message. On any failure the planned paths
are restored through ``GitRepo.discard_changes`` (a draft that already sat at a planned path is never deleted) and a
sanitized ``NoteWriteError`` is raised. An update that changes nothing is refused (git has nothing to commit).
Reads of whole listings take a shared lock so they never see half of an ``apply``.

``apply`` refuses to run on unclean state (``NoteWriteError``): a ``.git/index.lock`` (a running or interrupted git
command), anything staged (the paths are named), a note to update that is untracked or differs from ``HEAD``, a note
to create whose path exists or is known to git. The commit uses ``--only`` with the
planned paths, so nothing else can ever be committed. If a failure happens after the commit exists (``HEAD`` moved),
nothing is rolled back: ``apply`` returns the revision when ``HEAD`` is a commit with our message whose parent is the
previous ``HEAD``, and raises ``NoteWriteError`` (state unknown) when ``HEAD`` moved to anything else.
After a rollback, restored encrypted files get mode 0600 again. Leftover ``.<id>.md.<hex>.tmp`` files older than an
hour are deleted at the start of each ``apply``.

Residual risks: writers outside this class that change the tree between the clean-state check and the write are
overwritten (the lock covers only our writers); a killed ``git`` can leave a stale ``.git/index.lock`` that makes later
applies fail until the owner removes it (the error says so); ``get`` takes no lock unless the file looks uncommitted
(then it waits for any ``apply`` and checks again, so a read during a write is not refused spuriously); a file edited by
hand and restored again while it is being read can still be served once; git index flags set by hand
(``assume-unchanged``, ``skip-worktree``) hide edits from the check, so never set them on note files; replacing an
encrypted file by a plaintext one declaring ``internal`` is indistinguishable from a normal note; listing a folder reads
its entries by path (an attacker would need write access to the repository directory).
"""

import errno
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cerebro.adapters.outbound.crypto import RestrictedFormatError, decode_restricted, encode_restricted
from cerebro.adapters.outbound.filesystem.codec import NoteFormatError, parse_note_bytes, render_note
from cerebro.adapters.outbound.filesystem.errors import NoteFileError, NoteWriteError
from cerebro.adapters.outbound.filesystem.lock import file_lock
from cerebro.adapters.outbound.filesystem.safeio import (
    read_regular_file,
    remove_file,
    remove_stale_temp_files,
    set_mode,
    write_file_atomic,
)
from cerebro.adapters.outbound.git import GitError, GitRepo
from cerebro.application.errors import DecryptionError
from cerebro.application.ports import Cipher, NoteChange
from cerebro.application.ports.notes import CommittedCreate, FileProblem, NoteScan, check_agent_commit
from cerebro.domain.commit_message import AgentWriteCommit, OwnerCommit
from cerebro.domain.content_hash import content_hash, is_content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.vocabulary import CORE, HUB

FOLDERS: Final = ("core", "hubs", "notes")
MAX_FILE_BYTES: Final = 512 * 1024
"""Read cap for any note file: a 256 KiB note encrypts to about 350 KB."""
DEFAULT_LOCK_TIMEOUT: Final = 10.0
LOCK_FILE: Final = "cerebro-notes.lock"
STALE_TEMP_SECONDS: Final = 3600.0
_TEMP_PATTERN: Final = r"\.[a-z0-9-]+\.md\.[0-9a-f]{16}\.tmp"
_PLAIN_MODE: Final = 0o644
_RESTRICTED_MODE: Final = 0o600
UNCOMMITTED_REASON: Final = (
    "differs from the last commit (staged, edited, deleted or never committed): commit it, or discard it with"
    " git restore --staged --worktree (delete the file if it was never committed)"
)
_UNDECRYPTABLE: Final = "restricted note cannot be decrypted (wrong key, tampering or swapped id)"
_DUPLICATE_REASON: Final = "the same note id exists in several folders"
_INDEX_LOCK_HINT: Final = (
    "if no git command is running in the content repository, a stale .git/index.lock left by an interrupted git"
    " command blocks commits: remove that file"
)


def folder_for(note: Note) -> str:
    """The folder a note belongs in, from its type."""
    if note.type == CORE:
        return "core"
    if note.type == HUB:
        return "hubs"
    return "notes"


def _is_encrypted(data: bytes) -> bool:
    lines = data.split(b"\n", 4)
    return len(lines) >= 4 and lines[0] == b"---" and lines[3].startswith(b"encryption:")


def decode_note_file(relative: str, data: bytes, cipher: Cipher) -> Note:
    folder, _, name = relative.partition("/")
    stem = name.removesuffix(".md")
    try:
        if _is_encrypted(data):
            header_id, plaintext = decode_restricted(data, cipher)
            if header_id.value != stem:
                raise NoteFileError(relative, "the encrypted header id differs from the file name")
            note = parse_note_bytes(plaintext)
            if note.id != header_id:
                raise NoteFileError(relative, "the id inside the encrypted payload differs from the header")
            if note.sensitivity is not Sensitivity.RESTRICTED:
                raise NoteFileError(relative, "an encrypted note must declare sensitivity restricted")
        else:
            note = parse_note_bytes(data)
            if note.sensitivity is Sensitivity.RESTRICTED:
                raise NoteFileError(relative, "a plaintext file declares sensitivity restricted (refused)")
            if note.id.value != stem:
                raise NoteFileError(relative, "the note id differs from the file name")
    except RestrictedFormatError:
        raise NoteFileError(relative, "malformed restricted-note file") from None
    except DecryptionError:
        raise NoteFileError(relative, _UNDECRYPTABLE) from None
    except NoteFormatError as exc:
        raise NoteFileError(relative, exc.reason) from None
    if folder_for(note) != folder:
        raise NoteFileError(relative, f"a note of this type belongs in {folder_for(note)}/, not {folder}/")
    return note


@dataclass(frozen=True, slots=True)
class _Prepared:
    change: NoteChange
    relative: str
    data: bytes
    mode: int


class FilesystemNoteRepository:
    """See the module docstring. ``git`` must manage the repository rooted at ``root``."""

    def __init__(self, root: Path, *, git: GitRepo, cipher: Cipher, lock_timeout: float = DEFAULT_LOCK_TIMEOUT) -> None:
        if not root.is_dir():
            raise ValueError("content root is not a directory")
        self._root = root.resolve()
        dot_git = self._root / ".git"
        if dot_git.is_symlink() or not dot_git.is_dir():
            raise ValueError("the content root must be a git repository with a real .git directory")
        self._git = git
        self._cipher = cipher
        self._lock_timeout = lock_timeout
        self._lock_path = self._root / ".git" / LOCK_FILE

    # -- reading -------------------------------------------------------------------------------------------------

    def get(self, note_id: NoteId) -> Note | None:
        found = self._existing_paths(note_id)
        if not found:
            return None
        note, problem = self._lookup(found)
        if not self._uncommitted(found):  # checked after the read: what was read was not being edited
            return note  # ``None`` for a committed file that is not a valid note: left out, as in ``scan``
        # An ``apply`` may be between writing this file and committing it: wait for it (shared lock), then decide.
        with file_lock(self._lock_path, exclusive=False, timeout=self._lock_timeout):
            found = self._existing_paths(note_id)
            if not found:
                return None
            note, problem = self._lookup(found)
            if self._uncommitted(found):
                raise problem or NoteFileError(found[0], UNCOMMITTED_REASON)
            return note

    def _lookup(self, found: Sequence[str]) -> tuple[Note | None, NoteFileError | None]:
        """The note at the only path in ``found``, or ``None`` with the problem when the files themselves make it
        unusable (the same id in several folders, invalid content). Raises for anything else (I/O)."""
        if len(found) > 1:
            return None, NoteFileError(found[0], _DUPLICATE_REASON, stable=True)
        try:
            return self._read(found[0]), None
        except NoteFileError as exc:
            if not exc.stable:
                raise
            return None, exc

    def scan(self) -> NoteScan:
        """Checked before and after reading: a file edited by hand during the scan is reported too. A problem is
        stable only when it follows from the file's bytes or kind and the file equals ``HEAD``; files that differ
        from ``HEAD`` are left out (their content is never served) and reported as not stable."""
        with file_lock(self._lock_path, exclusive=False, timeout=self._lock_timeout):
            before = self._uncommitted(FOLDERS)
            notes, problems = self._load_all()
            uncommitted = set(before) | set(self._uncommitted(FOLDERS))
        reported = {problem.path for problem in problems}
        problems = [
            FileProblem(p.path, f"{p.reason}; it {UNCOMMITTED_REASON}", stable=False) if p.path in uncommitted else p
            for p in problems
        ]
        problems += [FileProblem(path, UNCOMMITTED_REASON, stable=False) for path in uncommitted - reported]
        return NoteScan(
            notes=tuple(note for path, note in notes if path not in uncommitted),
            problems=tuple(sorted(problems, key=lambda p: p.path)),
        )

    def list_notes(self) -> tuple[Note, ...]:
        scan = self.scan()
        if scan.problems:
            raise NoteFileError.from_problems([(p.path, p.reason) for p in scan.problems])
        return scan.notes

    def list_refs(self) -> tuple[NoteRef, ...]:
        return tuple(NoteRef.from_note(note) for note in self.list_notes())

    def head(self) -> str | None:
        return self._git.head()

    def find_create(self, request_key: str) -> CommittedCreate | None:
        if not is_content_hash(request_key):
            raise InvalidValueError("request_key", "expected a SHA-256 digest")
        try:
            found = self._git.find_create_commit(request_key)
        except GitError:
            raise NoteWriteError("could not inspect create history") from None
        if found is None:
            return None
        revision, message = found
        try:
            subject, _, rest = message.partition("\n\nWritten-by: ")
            commit = AgentWriteCommit(
                note_id=NoteId(subject.removeprefix("Add note: ")),
                written_by=rest.partition("\n")[0],
                request_key=request_key,
            )
            if commit.render() == message:
                # The note as the create stored it: ciphertext for restricted notes, nothing added to git.
                entry = self._git.historical_blob(
                    revision, [f"{folder}/{commit.note_id.value}.md" for folder in FOLDERS], MAX_FILE_BYTES
                )
                note = decode_note_file(*entry, self._cipher) if entry else None
                if note is not None and note.id == commit.note_id:
                    return CommittedCreate(revision=revision, note=note)
        except GitError, NoteFileError, InvalidValueError:
            pass
        raise NoteWriteError("invalid create record in content history")

    def _uncommitted(self, pathspecs: Sequence[str]) -> list[str]:
        """Note files the listings would read that differ from ``HEAD``: only committed content is ever served, so
        an index rebuilt from these files can be recorded as built from ``head()``. Other files are not checked."""
        try:
            paths = self._git.uncommitted_paths(pathspecs)
        except GitError:
            raise NoteFileError(pathspecs[0], "cannot compare the note files with the last commit") from None
        return [
            path
            for path in paths
            if (parts := path.split("/"))
            and len(parts) == 2
            and parts[1].endswith(".md")
            and not parts[1].startswith(".")
        ]

    def _existing_paths(self, note_id: NoteId) -> list[str]:
        for folder in FOLDERS:
            if (self._root / folder).is_symlink():
                raise NoteFileError(folder, "is not a real directory (symlink or file)")
        candidates = [f"{folder}/{note_id.value}.md" for folder in FOLDERS]
        return [rel for rel in candidates if os.path.lexists(self._root / rel)]

    def _read(self, relative: str) -> Note:
        """``NoteFileError.stable`` is set for invalid content and oversized files; never for an I/O failure (a
        symlinked file is refused as not stable too: its error cannot be told from a folder swapped meanwhile) nor
        for a restricted note that cannot be decrypted."""
        try:
            data = read_regular_file(self._root, relative, MAX_FILE_BYTES)
        except FileNotFoundError:
            raise NoteFileError(relative, "the file disappeared while reading") from None
        except OSError as exc:
            raise NoteFileError(
                relative, "cannot be read (symlink, not a regular file or too large)", stable=exc.errno == errno.EFBIG
            ) from None
        try:
            return decode_note_file(relative, data, self._cipher)
        except NoteFileError as exc:
            # A decryption failure depends on the configured keys, not on the file alone: a wrong key must fail
            # loudly, and fixing it must not leave restricted notes out of an index recorded as fresh.
            raise NoteFileError(exc.path, exc.reason, stable=exc.reason != _UNDECRYPTABLE) from None

    def _load_all(self) -> tuple[list[tuple[str, Note]], list[FileProblem]]:
        """``(path, note)`` pairs ordered by id, and every problem found (folder problems are never stable)."""
        entries: list[tuple[str, os.DirEntry[str]]] = []
        problems: list[FileProblem] = []
        for folder in FOLDERS:
            directory = self._root / folder
            try:
                is_real = directory.is_dir() and not directory.is_symlink()
                exists = directory.is_symlink() or directory.exists()
            except OSError:
                is_real, exists = False, True
            if not exists:
                continue
            if not is_real:
                problems.append(FileProblem(folder, "is not a real directory (symlink or file)", stable=False))
                continue
            try:
                entries.extend((folder, entry) for entry in sorted(os.scandir(directory), key=lambda e: e.name))
            except OSError:
                problems.append(FileProblem(folder, "cannot be listed", stable=False))
        names = [e.name for _, e in entries if e.name.endswith(".md") and not e.name.startswith(".")]
        duplicated = {name for name in names if names.count(name) > 1}
        notes: list[tuple[str, Note]] = []
        for folder, entry in entries:
            relative = f"{folder}/{entry.name}"
            if entry.name in duplicated:  # every copy is left out: none of them may stand for the id
                problems.append(FileProblem(relative, _DUPLICATE_REASON, stable=True))
            elif note := self._load_entry(relative, entry, problems):
                notes.append((relative, note))
        return sorted(notes, key=lambda item: item[1].id), problems

    def _load_entry(self, relative: str, entry: os.DirEntry[str], problems: list[FileProblem]) -> Note | None:
        name = entry.name
        if name.startswith("."):
            return None
        if entry.is_dir(follow_symlinks=False):
            problems.append(FileProblem(relative, "unexpected directory (the layout is flat)", stable=True))
            return None
        if not name.endswith(".md"):
            return None
        try:
            NoteId(name.removesuffix(".md"))
        except InvalidValueError:
            problems.append(FileProblem(relative, "the file name is not a valid note id", stable=True))
            return None
        try:
            return self._read(relative)
        except NoteFileError as exc:
            problems.append(FileProblem(relative, exc.reason, stable=exc.stable))
            return None

    # -- writing -------------------------------------------------------------------------------------------------

    def apply(self, changes: Sequence[NoteChange], message: OwnerCommit | AgentWriteCommit) -> str:
        if isinstance(message, AgentWriteCommit):
            check_agent_commit(changes, message)
        if not changes:
            raise InvalidValueError("changes", "at least one change is required")
        ids = [change.note.id for change in changes]
        if len(set(ids)) != len(ids):
            raise InvalidValueError("changes", "a note appears more than once")
        prepared = [self._prepare(change) for change in changes]
        with file_lock(self._lock_path, exclusive=True, timeout=self._lock_timeout):
            self._remove_stale_temp_files()
            self._check(prepared)
            before = self._check_clean(prepared)
            return self._write(prepared, message, before)

    def _prepare(self, change: NoteChange) -> _Prepared:
        note = change.note
        try:
            plaintext = render_note(note)
            if note.sensitivity is Sensitivity.RESTRICTED:
                data, mode = encode_restricted(note.id, plaintext, self._cipher), _RESTRICTED_MODE
            else:
                data, mode = plaintext, _PLAIN_MODE
        except (NoteFormatError, RestrictedFormatError) as exc:
            reason = exc.reason if isinstance(exc, NoteFormatError) else "restricted note is too large"
            raise InvalidValueError("note", reason) from None
        return _Prepared(change=change, relative=f"{folder_for(note)}/{note.id.value}.md", data=data, mode=mode)

    def _check(self, prepared: Sequence[_Prepared]) -> None:
        unchanged = 0
        for item in prepared:
            note, expected = item.change.note, item.change.expected_hash
            found = self._existing_paths(note.id)
            if expected is None:
                if found:
                    raise ContentConflict("", self._hash_or_empty(found[0]))
                continue
            if not found:
                raise ContentConflict(expected, "")
            if len(found) > 1:
                raise NoteFileError(found[0], _DUPLICATE_REASON)
            stored = self._read(found[0])
            if found[0] != item.relative:
                raise InvalidValueError("type", "a note cannot move between core/, hubs/ and notes/")
            actual = content_hash(stored)
            if actual != expected:
                raise ContentConflict(expected, actual)
            if stored == note:
                unchanged += 1
        if unchanged == len(prepared):
            raise NoteWriteError("nothing to commit: every note is unchanged")

    def _hash_or_empty(self, relative: str) -> str:
        try:
            return content_hash(self._read(relative))
        except NoteFileError:
            return ""

    def _remove_stale_temp_files(self) -> None:
        now = time.time()
        for folder in FOLDERS:
            remove_stale_temp_files(self._root, folder, older_than=STALE_TEMP_SECONDS, now=now, pattern=_TEMP_PATTERN)

    def _check_clean(self, prepared: Sequence[_Prepared]) -> frozenset[str]:
        """Refuse to write on top of uncommitted state, so that a rollback is exact.

        Nothing may be staged; every note being updated must be tracked in ``HEAD`` with unmodified bytes; no note
        being created may exist in the index or in ``HEAD`` (a file deleted by hand and recreated, for instance).
        """
        planned = [item.relative for item in prepared]
        if self._lock_hint():
            raise NoteWriteError(f"nothing was written{self._lock_hint()}")
        try:
            if staged := self._git.staged_paths():
                shown = ", ".join(ascii(path) for path in staged[:20])
                raise NoteWriteError(
                    f"the git index has staged changes ({shown}): commit them, or unstage them with"
                    " git restore --staged (an interrupted write leaves its note staged)"
                )
            before = self._git.snapshot_existing(planned)
            for item in prepared:
                if item.change.expected_hash is None:
                    if item.relative in before:
                        raise NoteWriteError("a note file for a new note is already known to git or exists")
                elif not self._git.matches_head(item.relative):
                    raise NoteWriteError("a note file is untracked or has uncommitted changes: commit or reset it")
        except GitError:
            raise NoteWriteError("could not inspect the content repository; nothing was written") from None
        return before

    def _write(
        self, prepared: Sequence[_Prepared], message: OwnerCommit | AgentWriteCommit, before: frozenset[str]
    ) -> str:
        planned = [item.relative for item in prepared]
        created = [item.relative for item in prepared if item.change.expected_hash is None]
        modified = [item.relative for item in prepared if item.change.expected_hash is not None]
        try:
            head_before = self._git.head()
        except GitError:
            raise NoteWriteError("could not inspect the content repository; nothing was written") from None
        rendered = message.render()
        try:
            for item in prepared:
                write_file_atomic(self._root, item.relative, item.data, mode=item.mode)
            self._git.stage(planned)
            return self._git.commit(rendered, planned)
        except BaseException as failure:
            try:
                now = self._git.head()
            except GitError:
                raise NoteWriteError("the change failed and the repository state is unknown: inspect it") from None
            if now == head_before:  # nothing was committed: restore the planned paths
                self._rollback(created, modified, before, failure)
                if isinstance(failure, OSError | GitError):
                    raise NoteWriteError(
                        f"the change could not be written or committed; nothing was kept{self._lock_hint()}"
                    ) from None
                raise
            if now is not None and self._is_our_commit(now, head_before, rendered):
                if isinstance(failure, Exception):
                    return now  # the commit exists and is ours: never roll it back
                raise
            raise NoteWriteError("the repository HEAD moved by someone else: state unknown, inspect it") from None

    def _is_our_commit(self, revision: str, parent: str | None, rendered: str) -> bool:
        try:
            parents, text = self._git.describe_commit(revision)
        except GitError:
            return False
        return parents == (() if parent is None else (parent,)) and text == rendered

    def _rollback(
        self, created: list[str], modified: list[str], before: frozenset[str], failure: BaseException
    ) -> None:
        try:
            self._git.discard_changes(created=created, modified=modified, preexisting=before)
            for relative in created:  # ``git clean`` skips ignored paths: remove what we created ourselves
                if relative not in before:
                    remove_file(self._root, relative)
            for relative in modified:
                self._restore_mode(relative)
        except GitError, OSError:
            if not isinstance(failure, Exception):
                raise failure from None  # an interrupt is never replaced by a different error
            raise NoteWriteError(
                f"the change failed and the rollback was incomplete: inspect the content repository{self._lock_hint()}"
            ) from None

    def _lock_hint(self) -> str:
        return f" ({_INDEX_LOCK_HINT})" if os.path.lexists(self._root / ".git" / "index.lock") else ""

    def _restore_mode(self, relative: str) -> None:
        """``git restore`` recreates files with the umask's mode: encrypted files go back to 0600."""
        try:
            if _is_encrypted(read_regular_file(self._root, relative, MAX_FILE_BYTES)):
                set_mode(self._root, relative, _RESTRICTED_MODE)
        except FileNotFoundError:
            return
