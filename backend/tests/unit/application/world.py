"""A small knowledge base wired to the in-memory fakes, for the read use cases."""

from dataclasses import dataclass, field
from typing import Any

from cerebro.application.ports.notes import NoteChange
from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Owner, Scope
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.vocabulary import CORE, HUB
from tests.fakes.notes import InMemoryNoteRepository
from tests.fakes.search import InMemoryGraphIndex, InMemorySearchIndex
from tests.unit.domain.builders import make_note

NID = NoteId
MESSAGE = CommitMessage(
    subject="Seed",
    proposal_id="proposal-0001",
    proposed_by="claude-code",
    curated_by="curator/null",
    approved_by="owner",
)


def ctx(
    level: Sensitivity = Sensitivity.INTERNAL,
    scopes: frozenset[Scope] = frozenset({Scope.READ}),
    *,
    owner: bool = False,
) -> AccessContext:
    ceiling = AccessCeiling(level)
    if owner:
        return AccessContext(principal=Owner(), ceiling=ceiling, scopes=frozenset())
    return AccessContext(principal=Agent("claude-code"), ceiling=ceiling, scopes=scopes)


@dataclass
class World:
    notes: InMemoryNoteRepository = field(default_factory=InMemoryNoteRepository)
    search: InMemorySearchIndex = field(default_factory=InMemorySearchIndex)
    graph: InMemoryGraphIndex = field(default_factory=InMemoryGraphIndex)

    def add(self, **overrides: Any) -> Note:
        defaults: dict[str, Any] = {"related": (), "hub": None, "sensitivity": Sensitivity.PUBLIC}
        note = make_note(**{**defaults, **overrides})
        revision = self.notes.apply([NoteChange(note=note)], MESSAGE)
        self.search.upsert(note)
        self.graph.upsert(NoteRef.from_note(note))
        self.search.set_revision(revision)
        self.graph.set_revision(revision)
        return note


def standard_world(*, leaky_links: bool = True) -> World:
    """A hub with members of every sensitivity and a superseded one, a core note, links between them.

    With ``leaky_links`` (the default) some notes link to notes of higher sensitivity: data that predates the
    ``link_to_higher_sensitivity`` rule or was edited outside Cérebro, which the read path must still serve safely.
    Pass ``leaky_links=False`` for a base that obeys the rule, as the write and maintenance paths expect.
    """
    w = World()
    w.add(
        id=NID("core-rules"),
        type=CORE,
        priority=Priority.P0,
        title="Core rules",
        body="Always be kind. See [[server-ssh-access]].\n" if leaky_links else "Always be kind.\n",
    )
    w.add(id=NID("infrastructure"), type=HUB, priority=Priority.P1, title="Infrastructure", body="Intro.\n")
    w.add(
        id=NID("server-ssh-access"),
        title="SSH access",
        hub=NID("infrastructure"),
        sensitivity=Sensitivity.INTERNAL,
        body="# SSH\n\nUse the key.\n\n## Verify\n\nCheck the host.\n\n# Other\n\nMore.\n",
        related=(NID("vault-layout"),) if leaky_links else (),
    )
    w.add(
        id=NID("vault-layout"),
        title="Vault layout",
        hub=NID("infrastructure"),
        sensitivity=Sensitivity.RESTRICTED,
        summary="Where the vault lives.",
        body="Secret layout, see [[server-ssh-access]].\n",
    )
    w.add(
        id=NID("old-proxy"),
        title="Old proxy",
        hub=NID("infrastructure"),
        status=Status.SUPERSEDED,
        superseded_by=NID("server-ssh-access"),
        sensitivity=Sensitivity.PUBLIC if leaky_links else Sensitivity.INTERNAL,
        priority=Priority.P3,
    )
    return w
