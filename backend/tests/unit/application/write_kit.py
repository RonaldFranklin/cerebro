"""The write path wired to the in-memory fakes, for the write use case tests."""

from dataclasses import dataclass, field

from cerebro.application.use_cases import (
    OwnerEditNote,
)
from cerebro.domain.access import AccessContext, Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.unit.application.world import NID, World, ctx, standard_world
from tests.unit.domain.builders import VOCABULARY

SECRET = "ghp_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"
SSH = NID("server-ssh-access")
READ_ONLY = frozenset({Scope.READ})


def agent_ctx(level: Sensitivity = Sensitivity.INTERNAL, scopes: frozenset[Scope] = READ_ONLY) -> AccessContext:
    return ctx(level, scopes)


def owner_ctx(level: Sensitivity = Sensitivity.RESTRICTED) -> AccessContext:
    return ctx(level, owner=True)


@dataclass
class Kit:
    world: World = field(default_factory=lambda: standard_world(leaky_links=False))
    clock: FixedClock = field(default_factory=FixedClock)
    ids: SequentialIdGenerator = field(default_factory=SequentialIdGenerator)

    def edit_uc(self) -> OwnerEditNote:
        return OwnerEditNote(self.world.notes, self.world.search, self.world.graph, self.clock, VOCABULARY)

    @property
    def commits(self) -> int:
        return len(self.world.notes.commits)


def note_hash(kit: Kit, note_id: NoteId = SSH) -> str:
    note = kit.world.notes.get(note_id)
    assert note is not None
    return content_hash(note)
