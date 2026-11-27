"""Composition root: wires the outbound adapters into the use cases.

Only this package may import adapters and use cases together. Inbound adapters never receive the ``Container``:
the entry point hands them individual use cases from ``Container.use_cases`` (use cases and DTOs only). The adapter
attributes of ``Container`` (``notes``, ``search``, ...) exist for tests and operations. The filesystem adapter serves
only committed note content: a note file that differs from ``HEAD`` fails the reads that touch it (and every rebuild),
so no index is ever recorded as built from ``HEAD`` with uncommitted content. A committed file that is not a valid
note is left out and named by the health report; it does not fail the other reads.
Startup, writes and rebuilds quarantine dirty checkout data under the state directory before restoring HEAD.

**Writer lock (ADR-0002).** Reading ``head()``, ``apply`` and advancing the index revision must be serialised,
otherwise a concurrent commit between them could make an index look fresh and hide a failed index update (see
``cerebro.application.index_state``). Each use case that commits or rebuilds indexes (``AgentWrite``,
``OwnerEditNote``, ``RebuildIndex``) runs under ``WriterLock``: one ``threading.Lock`` for the threads of this
process and one ``flock`` file in the state directory for other processes. The filesystem adapter keeps its own
lock inside ``.git/`` for the commit itself; the two never share a file, so they cannot deadlock.

**Self-healing indexes.** Every use case that reads an index, and every writer once it holds the lock, first
rebuilds both indexes when either is behind ``head()`` (``Refreshed``). A failed index update after a commit or an
external commit is therefore repaired by the next request, and a failed rebuild fails that request.
"""

import os
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from cerebro.adapters.outbound.crypto import FernetCipher, load_keys
from cerebro.adapters.outbound.filesystem import FilesystemNoteRepository, WriterBusyError, load_vocabulary
from cerebro.adapters.outbound.filesystem.backup_status import BackupStatusFile
from cerebro.adapters.outbound.filesystem.history import GitChangeHistory
from cerebro.adapters.outbound.filesystem.lock import file_lock
from cerebro.adapters.outbound.filesystem.quarantine import quarantine_changes
from cerebro.adapters.outbound.git import GitIdentity, GitRepo, GitVersionControl
from cerebro.adapters.outbound.sqlite import (
    SqliteGraphIndex,
    SqliteSearchIndex,
    SqliteTokenStore,
)
from cerebro.adapters.outbound.sqlite.login_attempts import SqliteLoginAttempts
from cerebro.adapters.outbound.sqlite.write_attempts import SqliteWriteAttemptStore
from cerebro.adapters.outbound.system import SystemIdGenerator, UtcClock
from cerebro.adapters.outbound.system.sessions import MemorySessions
from cerebro.application.agent_write import AgentWriteRequest, AgentWriteResult
from cerebro.application.browser import BrowserAuth
from cerebro.application.dto import (
    AgentTokenSummary,
    AuthenticateTokenRequest,
    CheckOwnerEditResult,
    GetGraphRequest,
    GetNeighborsRequest,
    GetOverviewRequest,
    GetVocabularyRequest,
    GraphView,
    HealthReportRequest,
    HealthReportResult,
    IssueAgentTokenRequest,
    IssuedToken,
    ListAgentTokensRequest,
    NoteView,
    Overview,
    OwnerEditNoteRequest,
    OwnerEditNoteResult,
    ReadNoteRequest,
    RebuildIndexRequest,
    RebuildIndexResult,
    RevokeAgentTokenRequest,
    RevokedToken,
    SearchNotesRequest,
    SearchResults,
)
from cerebro.application.index_state import index_is_stale
from cerebro.application.ports import (
    Cipher,
    Clock,
    GraphIndex,
    IdGenerator,
    NoteRepository,
    SearchIndex,
    TokenStore,
    VersionControl,
)
from cerebro.application.use_case import AnonymousUseCase, ContextUseCase
from cerebro.application.use_cases import (
    AuthenticateToken,
    CheckOwnerEdit,
    GetGraph,
    GetNeighbors,
    GetOverview,
    GetVocabulary,
    HealthReport,
    IssueAgentToken,
    ListAgentTokens,
    OwnerEditNote,
    ReadNote,
    RebuildIndex,
    RevokeAgentToken,
    SearchNotes,
)
from cerebro.application.use_cases.agent_write import AgentWrite
from cerebro.application.use_cases.history import OwnerHistory
from cerebro.bootstrap.browser import owner_credentials, private_runtime_directory
from cerebro.bootstrap.settings import Settings
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.vocabulary import Vocabulary

INDEX_FILE = "index.sqlite"
STATE_FILE = "state.sqlite"
WRITER_LOCK_FILE = "writer.lock"


def _prepare_state_dir(directory: Path) -> None:
    """Create the state directory 0700; an existing one must be ours and closed to others (never chmod-ed)."""
    try:
        if not directory.exists():
            directory.parent.mkdir(parents=True, exist_ok=True)
            directory.mkdir(mode=0o700)
        info = directory.stat()
    except OSError as error:
        raise StateDirectoryError(f"cannot prepare the state directory: {error.strerror}") from None
    if not stat.S_ISDIR(info.st_mode):
        raise StateDirectoryError("the state directory is not a directory")
    if info.st_uid != os.geteuid():
        raise StateDirectoryError("the state directory is not owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise StateDirectoryError("the state directory is more open than 0700 (it is never chmod-ed automatically)")


class StateDirectoryError(Exception):
    """The state directory exists but is not safe to use."""


class WriterLock:
    """One writer at a time across threads (``threading.Lock``) and processes (``flock`` on a file)."""

    def __init__(self, path: Path, *, timeout: float) -> None:
        self._path = path
        self._timeout = timeout
        self._thread_lock = threading.Lock()

    @contextmanager
    def held(self) -> Iterator[None]:
        if not self._thread_lock.acquire(timeout=self._timeout):
            raise WriterBusyError("the writer lock is held by another writer")
        try:
            with file_lock(self._path, exclusive=True, timeout=self._timeout, label="writer lock"):
                yield
        finally:
            self._thread_lock.release()


class Serialized[Request, Result]:
    """Runs the wrapped use case under the writer lock; same ``execute`` signature."""

    def __init__(self, inner: ContextUseCase[Request, Result], lock: WriterLock) -> None:
        self._inner = inner
        self._lock = lock

    def execute(self, request: Request, ctx: AccessContext) -> Result:
        with self._lock.held():
            return self._inner.execute(request, ctx)


class Refreshed[Request, Result]:
    """Brings both indexes up to ``head()`` before running the wrapped use case; same ``execute`` signature."""

    def __init__(self, inner: ContextUseCase[Request, Result], refresh: Callable[[], object]) -> None:
        self._inner = inner
        self._refresh = refresh

    def execute(self, request: Request, ctx: AccessContext) -> Result:
        self._refresh()
        return self._inner.execute(request, ctx)


@dataclass(frozen=True, slots=True, kw_only=True)
class UseCases:
    """What inbound adapters may call. Use cases that commit or rewrite the indexes are serialised."""

    get_overview: ContextUseCase[GetOverviewRequest, Overview]
    search_notes: ContextUseCase[SearchNotesRequest, SearchResults]
    read_note: ContextUseCase[ReadNoteRequest, NoteView]
    get_neighbors: ContextUseCase[GetNeighborsRequest, GraphView]
    get_graph: ContextUseCase[GetGraphRequest, GraphView]
    get_vocabulary: ContextUseCase[GetVocabularyRequest, Vocabulary]
    agent_write: ContextUseCase[AgentWriteRequest, AgentWriteResult]
    history: OwnerHistory
    owner_edit_note: ContextUseCase[OwnerEditNoteRequest, OwnerEditNoteResult]
    check_owner_edit: ContextUseCase[OwnerEditNoteRequest, CheckOwnerEditResult]
    rebuild_index: ContextUseCase[RebuildIndexRequest, RebuildIndexResult]
    health_report: ContextUseCase[HealthReportRequest, HealthReportResult]
    issue_agent_token: ContextUseCase[IssueAgentTokenRequest, IssuedToken]
    list_agent_tokens: ContextUseCase[ListAgentTokensRequest, tuple[AgentTokenSummary, ...]]
    revoke_agent_token: ContextUseCase[RevokeAgentTokenRequest, RevokedToken]
    authenticate_token: AnonymousUseCase[AuthenticateTokenRequest, AccessContext]


class Container:
    """The wired process. ``start()`` must run before serving: it reindexes a stale or missing index.

    The adapters (``notes``, ``search``, ``graph``, ...) are attributes for the composition root, tests and
    operations; inbound adapters are given ``use_cases`` only.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        state_dir = settings.state_dir
        _prepare_state_dir(state_dir)
        self.writer_lock = WriterLock(state_dir / WRITER_LOCK_FILE, timeout=settings.writer_lock_timeout)

        self.clock: Clock = UtcClock()
        self.ids: IdGenerator = SystemIdGenerator()
        self.cipher: Cipher = FernetCipher(load_keys(settings.key_file))
        git = GitRepo(settings.content_repo, identity=GitIdentity(name=settings.git_name, email=settings.git_email))
        self.version_control: VersionControl = GitVersionControl(
            git, remote=settings.git_remote, branch=settings.git_branch
        )
        self.notes: NoteRepository = FilesystemNoteRepository(settings.content_repo, git=git, cipher=self.cipher)

        def quarantine() -> None:
            quarantine_changes(settings.content_repo, state_dir, git, settings.writer_lock_timeout)

        self._quarantine = quarantine
        self._git = git
        index_dir = state_dir
        if settings.index_dir is not None:
            index_dir = private_runtime_directory(settings.index_dir, settings.content_repo, "index")
            _prepare_state_dir(index_dir)
        self.search: SearchIndex = SqliteSearchIndex(index_dir / INDEX_FILE)
        self.graph: GraphIndex = SqliteGraphIndex(index_dir / INDEX_FILE)
        self.tokens: TokenStore = SqliteTokenStore(state_dir / STATE_FILE)

        self._rebuild = RebuildIndex(self.notes, self.search, self.graph)

    @cached_property
    def vocabulary(self) -> Vocabulary:
        return load_vocabulary(self.settings.content_repo)

    @cached_property
    def use_cases(self) -> UseCases:
        state_dir = self.settings.state_dir
        notes, search, graph = self.notes, self.search, self.graph
        clock, ids, vocabulary, lock = self.clock, self.ids, self.vocabulary, self.writer_lock
        write = AgentWrite(
            notes,
            search,
            graph,
            SqliteWriteAttemptStore(state_dir / STATE_FILE),
            clock,
            ids,
            vocabulary,
            self.settings.writes_per_hour,
        )
        fresh, fresh_locked = self.refresh, self._refresh_locked
        return UseCases(
            agent_write=Serialized(Refreshed(write, fresh_locked), lock),
            get_overview=Refreshed(GetOverview(notes, search), fresh),
            search_notes=Refreshed(SearchNotes(search), fresh),
            read_note=Refreshed(ReadNote(notes, graph), fresh),
            get_neighbors=Refreshed(GetNeighbors(notes, graph), fresh),
            get_graph=Refreshed(GetGraph(graph), fresh),
            get_vocabulary=GetVocabulary(vocabulary),
            history=OwnerHistory(notes, GitChangeHistory(self._git, self.cipher), lock.held),
            owner_edit_note=Serialized(
                Refreshed(OwnerEditNote(notes, search, graph, clock, vocabulary), fresh_locked), lock
            ),
            # Reads the repository alone: no index to refresh, and a check must not write.
            check_owner_edit=CheckOwnerEdit(notes, clock, vocabulary),
            rebuild_index=Serialized(Refreshed(self._rebuild, self._quarantine), lock),
            health_report=HealthReport(
                notes,
                search,
                graph,
                clock,
                vocabulary,
                quarantines=lambda: sorted(p.name for p in state_dir.glob("quarantine-*") if p.is_dir()),
                backup=BackupStatusFile(state_dir / "last-backup.json"),
            ),
            issue_agent_token=IssueAgentToken(self.tokens, clock, ids),
            list_agent_tokens=ListAgentTokens(self.tokens),
            revoke_agent_token=RevokeAgentToken(self.tokens, clock),
            authenticate_token=AuthenticateToken(self.tokens, clock),
        )

    @cached_property
    def browser_auth(self) -> BrowserAuth:
        """Built once at browser service startup and shared between its listener adapters."""
        return BrowserAuth(
            owner_credentials(self.settings),
            SqliteLoginAttempts(self.settings.state_dir / STATE_FILE),
            MemorySessions(),
            self.clock,
            self.ids,
        )

    def prepare_checkout(self) -> None:
        """Allow an explicit CLI reindex to restore the schema before its use cases load the vocabulary."""
        with self.writer_lock.held():
            self._quarantine()

    def start(self) -> bool:
        """Reindex when either index was not built from the repository's current revision (never built, an
        out-of-band commit, or an index file that was recreated after corruption). Returns whether it did.
        Nothing about the notes is logged or returned."""
        with self.writer_lock.held():
            return self._refresh_locked()

    def refresh(self) -> None:
        """Runs before every index read. Unlocked, it only compares the recorded revisions with ``head()`` (one
        ``git rev-parse``); when an index is behind (a failed index update after a commit, an external commit), it
        rebuilds under the writer lock. A failed rebuild raises, so a read fails rather than serve stale rows."""
        if index_is_stale(self.notes, self.search, self.graph):
            with self.writer_lock.held():
                self._refresh_locked()

    def _refresh_locked(self) -> bool:
        self._quarantine()
        if not index_is_stale(self.notes, self.search, self.graph):
            return False
        owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
        # Already under the (non-reentrant) writer lock: call the unwrapped use case.
        self._rebuild.execute(RebuildIndexRequest(), owner)
        return True


def build_container(settings: Settings) -> Container:
    """Wire every adapter and use case for ``settings``. Call ``Container.start()`` before serving."""
    return Container(settings)
