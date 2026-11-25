"""Composition root: wires the outbound adapters into the use cases.

Only this package may import adapters and use cases together. Inbound adapters receive ``Container.use_cases``
(use cases and DTOs only) and nothing else.

**Writer lock (ADR-0002).** Reading ``head()``, ``apply`` and advancing the index revision must be serialised,
otherwise a concurrent commit between them could make an index look fresh and hide a failed index update (see
``cerebro.application.index_state``). Every use case that commits or rewrites the indexes (``ApplyProposal``,
``OwnerEditNote``, ``RebuildIndex``) runs under ``WriterLock``: one ``threading.Lock`` for the threads of this
process and one ``flock`` file in the state directory for other processes. The filesystem adapter keeps its own
lock inside ``.git/`` for the commit itself; the two never share a file, so they cannot deadlock.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cerebro.adapters.outbound.crypto import FernetCipher, load_keys
from cerebro.adapters.outbound.filesystem import FilesystemNoteRepository, load_vocabulary
from cerebro.adapters.outbound.filesystem.lock import file_lock
from cerebro.adapters.outbound.git import GitIdentity, GitRepo, GitVersionControl
from cerebro.adapters.outbound.sqlite import (
    SqliteGraphIndex,
    SqliteProposalStore,
    SqliteSearchIndex,
    SqliteTokenStore,
)
from cerebro.adapters.outbound.system import SystemIdGenerator, UtcClock
from cerebro.application.dto import (
    ApplyProposalResult,
    AuthenticateTokenRequest,
    GetGraphRequest,
    GetNeighborsRequest,
    GetOverviewRequest,
    GraphView,
    HealthReportRequest,
    HealthReportResult,
    IssueAgentTokenRequest,
    IssuedToken,
    NoteView,
    Overview,
    OwnerEditNoteRequest,
    OwnerEditNoteResult,
    ProposalIdRequest,
    ProposalOutcome,
    ReadNoteRequest,
    RebuildIndexRequest,
    RebuildIndexResult,
    ReviewProposalRequest,
    RevokeAgentTokenRequest,
    RevokedToken,
    SearchNotesRequest,
    SearchResults,
    SubmitProposalRequest,
)
from cerebro.application.index_state import index_is_stale
from cerebro.application.null_curator import NullCurator
from cerebro.application.ports import (
    Cipher,
    Clock,
    Curator,
    GraphIndex,
    IdGenerator,
    NoteRepository,
    ProposalStore,
    SearchIndex,
    TokenStore,
    VersionControl,
)
from cerebro.application.use_cases import (
    ApplyProposal,
    AuthenticateToken,
    CurateProposal,
    GetGraph,
    GetNeighbors,
    GetOverview,
    HealthReport,
    IssueAgentToken,
    OwnerEditNote,
    ReadNote,
    RebuildIndex,
    ReviewProposal,
    RevokeAgentToken,
    SearchNotes,
    SubmitProposal,
    ValidateProposal,
)
from cerebro.bootstrap.settings import Settings
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.vocabulary import Vocabulary

INDEX_FILE = "index.sqlite"
STATE_FILE = "state.sqlite"
WRITER_LOCK_FILE = "writer.lock"


class ContextUseCase[Request, Result](Protocol):
    def execute(self, request: Request, ctx: AccessContext) -> Result: ...


class AnonymousUseCase[Request, Result](Protocol):
    def execute(self, request: Request) -> Result: ...


class WriterLock:
    """One writer at a time across threads (``threading.Lock``) and processes (``flock`` on a file)."""

    def __init__(self, path: Path, *, timeout: float) -> None:
        self._path = path
        self._timeout = timeout
        self._thread_lock = threading.Lock()

    @contextmanager
    def held(self) -> Iterator[None]:
        with self._thread_lock, file_lock(self._path, exclusive=True, timeout=self._timeout):
            yield


class Serialized[Request, Result]:
    """Runs the wrapped use case under the writer lock; same ``execute`` signature."""

    def __init__(self, inner: ContextUseCase[Request, Result], lock: WriterLock) -> None:
        self._inner = inner
        self._lock = lock

    def execute(self, request: Request, ctx: AccessContext) -> Result:
        with self._lock.held():
            return self._inner.execute(request, ctx)


@dataclass(frozen=True, slots=True, kw_only=True)
class UseCases:
    """What inbound adapters may call. Use cases that commit or rewrite the indexes are serialised."""

    get_overview: ContextUseCase[GetOverviewRequest, Overview]
    search_notes: ContextUseCase[SearchNotesRequest, SearchResults]
    read_note: ContextUseCase[ReadNoteRequest, NoteView]
    get_neighbors: ContextUseCase[GetNeighborsRequest, GraphView]
    get_graph: ContextUseCase[GetGraphRequest, GraphView]
    submit_proposal: ContextUseCase[SubmitProposalRequest, ProposalOutcome]
    validate_proposal: ContextUseCase[ProposalIdRequest, ProposalOutcome]
    curate_proposal: ContextUseCase[ProposalIdRequest, ProposalOutcome]
    review_proposal: ContextUseCase[ReviewProposalRequest, ProposalOutcome]
    apply_proposal: ContextUseCase[ProposalIdRequest, ApplyProposalResult]
    owner_edit_note: ContextUseCase[OwnerEditNoteRequest, OwnerEditNoteResult]
    rebuild_index: ContextUseCase[RebuildIndexRequest, RebuildIndexResult]
    health_report: ContextUseCase[HealthReportRequest, HealthReportResult]
    issue_agent_token: ContextUseCase[IssueAgentTokenRequest, IssuedToken]
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
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.writer_lock = WriterLock(state_dir / WRITER_LOCK_FILE, timeout=settings.writer_lock_timeout)

        self.clock: Clock = UtcClock()
        self.ids: IdGenerator = SystemIdGenerator()
        self.cipher: Cipher = FernetCipher(load_keys(settings.key_file))
        git = GitRepo(settings.content_repo, identity=GitIdentity(name=settings.git_name, email=settings.git_email))
        self.version_control: VersionControl = GitVersionControl(
            git, remote=settings.git_remote, branch=settings.git_branch
        )
        self.notes: NoteRepository = FilesystemNoteRepository(settings.content_repo, git=git, cipher=self.cipher)
        self.vocabulary: Vocabulary = load_vocabulary(settings.content_repo)
        self.search: SearchIndex = SqliteSearchIndex(state_dir / INDEX_FILE)
        self.graph: GraphIndex = SqliteGraphIndex(state_dir / INDEX_FILE)
        self.proposals: ProposalStore = SqliteProposalStore(state_dir / STATE_FILE, self.cipher)
        self.tokens: TokenStore = SqliteTokenStore(state_dir / STATE_FILE)
        self.curator: Curator = NullCurator()

        notes, proposals, search, graph = self.notes, self.proposals, self.search, self.graph
        clock, ids, vocabulary, lock = self.clock, self.ids, self.vocabulary, self.writer_lock
        self._rebuild = RebuildIndex(notes, search, graph)
        self.use_cases = UseCases(
            get_overview=GetOverview(notes, search),
            search_notes=SearchNotes(search),
            read_note=ReadNote(notes, graph),
            get_neighbors=GetNeighbors(notes, graph),
            get_graph=GetGraph(graph),
            submit_proposal=SubmitProposal(notes, proposals, clock, ids),
            validate_proposal=ValidateProposal(notes, proposals, search, clock, vocabulary),
            curate_proposal=CurateProposal(notes, proposals, search, self.curator, clock),
            review_proposal=ReviewProposal(notes, proposals, clock),
            apply_proposal=Serialized(ApplyProposal(notes, proposals, search, graph, clock, vocabulary), lock),
            owner_edit_note=Serialized(OwnerEditNote(notes, search, graph, clock, ids, vocabulary), lock),
            rebuild_index=Serialized(self._rebuild, lock),
            health_report=HealthReport(notes, proposals, search, graph, clock, vocabulary),
            issue_agent_token=IssueAgentToken(self.tokens, clock, ids),
            revoke_agent_token=RevokeAgentToken(self.tokens, clock),
            authenticate_token=AuthenticateToken(self.tokens, clock),
        )

    def start(self) -> bool:
        """Reindex when either index was not built from the repository's current revision (never built, an
        out-of-band commit, or an index file that was recreated after corruption). Returns whether it did.
        Nothing about the notes is logged or returned."""
        with self.writer_lock.held():
            if not index_is_stale(self.notes, self.search, self.graph):
                return False
            owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
            # Already under the (non-reentrant) writer lock: call the unwrapped use case.
            self._rebuild.execute(RebuildIndexRequest(), owner)
            return True


def build_container(settings: Settings) -> Container:
    """Wire every adapter and use case for ``settings``. Call ``Container.start()`` before serving."""
    return Container(settings)
