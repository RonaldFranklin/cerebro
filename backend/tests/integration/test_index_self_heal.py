"""Stale index state never serves a wider projection: real Git, SQLite and the composition root.

A note is narrowed (raised to restricted, or archived) while an index update fails at every step. The next read
rebuilds before it answers, or fails; it never returns the note's title or summary to a narrower caller.
"""

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from cerebro.adapters.outbound.crypto import FernetCipher, load_keys
from cerebro.adapters.outbound.filesystem import FilesystemNoteRepository, NoteWriteError
from cerebro.adapters.outbound.filesystem.codec import render_note
from cerebro.adapters.outbound.filesystem.repository import UNCOMMITTED_REASON
from cerebro.application.agent_write import AgentWriteRequest
from cerebro.application.dto import (
    AuthenticateTokenRequest,
    GetGraphRequest,
    GetOverviewRequest,
    HealthCode,
    HealthReportRequest,
    IssueAgentTokenRequest,
    OwnerEditNoteRequest,
    ReadNoteRequest,
    SearchNotesRequest,
)
from cerebro.application.errors import NoteNotFound, NoteReadError
from cerebro.application.index_state import index_is_stale
from cerebro.application.ports.notes import NoteChange
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.commit_message import OwnerCommit
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity, Status
from cerebro.domain.note import Note, NoteId
from tests.integration.content_kit import HUB_ID, SampleRepo, make_sample_repo
from tests.unit.domain.builders import VALID_SUMMARY

OWNER = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
MARKER = "quokkaplan"
FIELDS = {
    "title": f"Restart procedure {MARKER}",
    "type": "guide",
    "area": "infrastructure",
    "priority": "P2",
    "sensitivity": "internal",
    "hub": HUB_ID.value,
    "summary": f"{VALID_SUMMARY} {MARKER}",
}
NARROWINGS = {"restricted": {"sensitivity": "restricted"}, "archived": {"status": "archived"}}


def boom(*args: object, **kwargs: object) -> None:
    raise OSError("synthetic index failure")


class Service:
    def __init__(self, sample: SampleRepo) -> None:
        self.sample = sample
        self.container: Container = build_container(sample.settings)
        self.container.start()
        issued = self.container.use_cases.issue_agent_token.execute(
            IssueAgentTokenRequest(agent="codex", scopes=frozenset({Scope.READ, Scope.WRITE}), ceiling=OWNER.ceiling),
            OWNER,
        )
        authenticate = self.container.use_cases.authenticate_token.execute
        self.writer = authenticate(AuthenticateTokenRequest(secret=issued.secret, listener=OWNER.ceiling))
        self.reader = authenticate(
            AuthenticateTokenRequest(secret=issued.secret, listener=AccessCeiling(Sensitivity.INTERNAL))
        )
        self.note_id = self.container.use_cases.agent_write.execute(
            AgentWriteRequest(operation="create", request_id="r-1", frontmatter=FIELDS, body="Check status first."),
            self.writer,
        ).note_id

    def note(self) -> Note:
        note = self.container.notes.get(self.note_id)
        assert note is not None
        return note

    def narrow(self, change: dict[str, str]) -> None:
        self.container.use_cases.agent_write.execute(
            AgentWriteRequest(
                operation="update",
                target=self.note_id.value,
                base_hash=content_hash(self.note()),
                frontmatter={**FIELDS, **change},
                body="Check status first.",
            ),
            self.writer,
        )

    def visible_to_reader(self) -> bool:
        """Whether the internal reader sees the note's title or summary through any index read."""
        uc = self.container.use_cases
        found = uc.search_notes.execute(SearchNotesRequest(text=MARKER), self.reader).results
        graph = uc.get_graph.execute(GetGraphRequest(), self.reader).nodes
        overview = repr(uc.get_overview.execute(GetOverviewRequest(), self.reader))
        return (
            any(hit.note.id == self.note_id for hit in found)
            or any(node.id == self.note_id for node in graph)
            or MARKER in overview
            or self.note_id in self.hub_members(include_history=False)
        )

    def hub_members(self, *, include_history: bool) -> list[NoteId]:
        """Hub members come from the graph index's backlinks."""
        hub = self.container.use_cases.read_note.execute(
            ReadNoteRequest(note_id=HUB_ID, include_history=include_history), self.reader
        )
        return [member.id for member in hub.members or ()]

    def history_view(self) -> set[Status]:
        """Statuses under which the reader finds the note when it asks for history (search, read, hub)."""
        uc = self.container.use_cases
        found = uc.search_notes.execute(SearchNotesRequest(text=MARKER, include_history=True), self.reader).results
        statuses = {hit.note.status for hit in found if hit.note.id == self.note_id}
        with contextlib.suppress(NoteNotFound):
            statuses.add(uc.read_note.execute(ReadNoteRequest(note_id=self.note_id), self.reader).status)
        if self.note_id in self.hub_members(include_history=True):
            statuses.add(self.note().status)
        return statuses

    def stale(self) -> bool:
        c = self.container
        return index_is_stale(c.notes, c.search, c.graph)


@pytest.fixture
def service(tmp_path: Path) -> Service:
    service = Service(make_sample_repo(tmp_path))
    assert service.visible_to_reader()
    assert not service.stale()
    return service


def commit_then_fail(apply: Callable[..., str]) -> Callable[..., str]:
    def failing(changes: Sequence[NoteChange], message: object) -> str:
        apply(changes, message)
        raise NoteWriteError("state unknown after the commit")

    return failing


@pytest.mark.parametrize("narrowing", NARROWINGS)
@pytest.mark.parametrize(
    "failure", ["search.upsert", "graph.upsert", "search.set_revision", "graph.set_revision", "notes.apply"]
)
def test_a_failed_index_update_never_serves_the_wider_projection(
    service: Service, monkeypatch: pytest.MonkeyPatch, narrowing: str, failure: str
) -> None:
    port, method = failure.split(".")
    target = getattr(service.container, port)
    with monkeypatch.context() as patch:
        if failure == "notes.apply":
            patch.setattr(target, method, commit_then_fail(target.apply))
            with pytest.raises(NoteWriteError):
                service.narrow(NARROWINGS[narrowing])
        else:
            patch.setattr(target, method, boom)
            service.narrow(NARROWINGS[narrowing])
    assert service.stale()
    assert not service.visible_to_reader()
    assert not service.stale()
    # History reads: an archived note stays readable as archived, a restricted one is gone.
    assert service.history_view() == ({Status.ARCHIVED} if narrowing == "archived" else set())


def test_reads_fail_rather_than_serve_stale_rows_when_the_rebuild_fails(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        for method in ("upsert", "set_revision", "rebuild"):
            patch.setattr(service.container.search, method, boom)
        service.narrow(NARROWINGS["restricted"])
        with pytest.raises(OSError, match="synthetic"):
            service.container.use_cases.search_notes.execute(SearchNotesRequest(text=MARKER), service.reader)
        with pytest.raises(OSError, match="synthetic"):
            service.container.use_cases.get_overview.execute(GetOverviewRequest(), service.reader)
    assert not service.visible_to_reader()


def test_a_failed_index_update_after_an_owner_edit_heals_on_the_next_read(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = service.note()
    with monkeypatch.context() as patch:
        patch.setattr(service.container.graph, "upsert", boom)
        result = service.container.use_cases.owner_edit_note.execute(
            OwnerEditNoteRequest(
                note=replace(note, sensitivity=Sensitivity.RESTRICTED), expected_hash=content_hash(note)
            ),
            OWNER,
        )
    assert result.revision == service.container.notes.head()
    assert not service.visible_to_reader()


def test_a_failed_commit_leaves_the_index_serving_the_unchanged_note(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = service.container.notes.head()
    with monkeypatch.context() as patch:
        patch.setattr(service.container.notes, "apply", boom)
        with pytest.raises(OSError, match="synthetic"):
            service.narrow(NARROWINGS["archived"])
    assert service.container.notes.head() == before
    assert not service.stale()
    assert service.visible_to_reader()


def test_external_commits_become_visible_without_a_restart(service: Service) -> None:
    settings = service.sample.settings
    outside = FilesystemNoteRepository(
        settings.content_repo, git=service.sample.git(), cipher=FernetCipher(load_keys(settings.key_file))
    )
    note = service.note()
    outside.apply(
        [NoteChange(note=replace(note, status=Status.ARCHIVED), expected_hash=content_hash(note))],
        OwnerCommit(subject="Archive outside the service"),
    )
    assert not service.visible_to_reader()
    added = service.sample.commit_out_of_band(replace(note, id=NoteId("added-outside"), status=Status.CURRENT))
    assert service.container.notes.head() == added
    found = service.container.use_cases.search_notes.execute(SearchNotesRequest(text=MARKER), service.reader)
    assert [hit.note.id for hit in found.results] == [NoteId("added-outside")]


def test_an_abandoned_uncommitted_edit_never_reaches_a_rebuilt_index(service: Service) -> None:
    """The owner starts lowering a restricted note by hand, a commit moves HEAD meanwhile, then the edit is dropped."""
    note = service.note()
    service.container.use_cases.owner_edit_note.execute(
        OwnerEditNoteRequest(note=replace(note, sensitivity=Sensitivity.RESTRICTED), expected_hash=content_hash(note)),
        OWNER,
    )
    assert not service.visible_to_reader()
    relative = f"notes/{service.note_id.value}.md"
    (service.sample.root / relative).write_bytes(render_note(note))  # in progress: back to internal, plaintext
    service.sample.commit_out_of_band(replace(note, id=NoteId("added-outside")))
    assert service.stale()
    with pytest.raises(NoteReadError) as refused:
        service.container.use_cases.search_notes.execute(SearchNotesRequest(text=MARKER), service.reader)
    assert refused.value.problems == ((relative, UNCOMMITTED_REASON),)
    with pytest.raises(NoteReadError):
        service.container.use_cases.read_note.execute(ReadNoteRequest(note_id=service.note_id), service.reader)
    assert service.stale()  # nothing was recorded as built from HEAD

    service.sample.git().discard_changes(created=[], modified=[relative])  # the owner drops the edit
    found = service.container.use_cases.search_notes.execute(SearchNotesRequest(text=MARKER), service.reader)
    assert [hit.note.id for hit in found.results] == [NoteId("added-outside")]
    assert not service.stale()


def test_one_invalid_committed_file_is_left_out_without_a_rebuild_per_request(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = replace(service.note(), id=NoteId("broken-secret"), sensitivity=Sensitivity.RESTRICTED)
    relative = f"notes/{hidden.id.value}.md"
    (service.sample.root / relative).write_text("---\nnot: [valid\n---\n")
    service.sample.git().stage([relative])
    service.sample.git().commit("Add a broken note by hand", [relative])
    uc = service.container.use_cases
    assert service.visible_to_reader()  # the rebuild before the read left the broken file out
    assert not service.stale()
    rebuilds: list[object] = []
    monkeypatch.setattr(service.container.search, "rebuild", lambda *a, **k: rebuilds.append(a))
    assert uc.read_note.execute(ReadNoteRequest(note_id=service.note_id), service.reader).id == service.note_id
    for ctx in (service.reader, service.writer):  # missing for everyone: nothing of it can be served
        with pytest.raises(NoteNotFound):
            uc.read_note.execute(ReadNoteRequest(note_id=hidden.id), ctx)
    assert rebuilds == []
    report = uc.health_report.execute(HealthReportRequest(), OWNER)
    assert [(i.subject, i.code) for i in report.issues if i.code is HealthCode.NOTE_FILE_EXCLUDED] == [
        (relative, HealthCode.NOTE_FILE_EXCLUDED)
    ]
