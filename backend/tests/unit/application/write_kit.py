"""The write path wired to the in-memory fakes, for the write use case tests."""

from dataclasses import dataclass, field
from typing import Any

from cerebro.application.dto import (
    ApplyProposalResult,
    ProposalIdRequest,
    ProposalOutcome,
    ReviewAction,
    ReviewProposalRequest,
    SubmitProposalRequest,
)
from cerebro.application.null_curator import NullCurator
from cerebro.application.ports.curator import Curator
from cerebro.application.use_cases import (
    ApplyProposal,
    CurateProposal,
    OwnerEditNote,
    ReviewProposal,
    SubmitProposal,
    ValidateProposal,
)
from cerebro.domain.access import AccessContext, Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.decision import Decision
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import Proposal, ProposalKind, ProposedContent
from cerebro.domain.vocabulary import Area, NoteType
from tests.fakes.stores import InMemoryProposalStore
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.unit.application.world import NID, World, ctx, standard_world
from tests.unit.domain.builders import VALID_SUMMARY, VOCABULARY

SECRET = "ghp_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"
SSH = NID("server-ssh-access")
NEW_ID = NID("docker-conventions")
PROPOSE = frozenset({Scope.READ, Scope.PROPOSE})


def agent_ctx(level: Sensitivity = Sensitivity.INTERNAL, scopes: frozenset[Scope] = PROPOSE) -> AccessContext:
    return ctx(level, scopes)


def owner_ctx(level: Sensitivity = Sensitivity.RESTRICTED) -> AccessContext:
    return ctx(level, owner=True)


def new_content(**overrides: Any) -> ProposedContent:
    fields: dict[str, Any] = {
        "title": "Docker conventions",
        "body": "# Docker\n\nUse compose for every service.\n",
        "area": Area("infrastructure"),
        "type": NoteType("guide"),
        "priority": Priority.P2,
        "sensitivity": Sensitivity.PUBLIC,
        "summary": VALID_SUMMARY,
        "hub": NID("infrastructure"),
        "rationale": "agents keep asking",
    }
    fields.update(overrides)
    return ProposedContent(**fields)


@dataclass
class Kit:
    world: World = field(default_factory=lambda: standard_world(leaky_links=False))
    proposals: Any = field(default_factory=InMemoryProposalStore)
    clock: FixedClock = field(default_factory=FixedClock)
    ids: SequentialIdGenerator = field(default_factory=SequentialIdGenerator)
    curator: Curator = field(default_factory=NullCurator)

    @property
    def store(self) -> InMemoryProposalStore:
        store: InMemoryProposalStore = self.proposals
        return store

    def submit_uc(self) -> SubmitProposal:
        return SubmitProposal(self.world.notes, self.proposals, self.clock, self.ids)

    def validate_uc(self) -> ValidateProposal:
        return ValidateProposal(self.world.notes, self.proposals, self.world.search, self.clock, VOCABULARY)

    def curate_uc(self) -> CurateProposal:
        return CurateProposal(self.world.notes, self.proposals, self.world.search, self.curator, self.clock)

    def review_uc(self) -> ReviewProposal:
        return ReviewProposal(self.world.notes, self.proposals, self.clock)

    def apply_uc(self) -> ApplyProposal:
        return ApplyProposal(
            self.world.notes, self.proposals, self.world.search, self.world.graph, self.clock, VOCABULARY
        )

    def edit_uc(self) -> OwnerEditNote:
        return OwnerEditNote(self.world.notes, self.world.search, self.world.graph, self.clock, self.ids, VOCABULARY)

    # -- steps -------------------------------------------------------------------------------------------------

    def submit(self, request: SubmitProposalRequest, c: AccessContext | None = None) -> ProposalOutcome:
        return self.submit_uc().execute(request, c or agent_ctx())

    def submit_create(self, **overrides: Any) -> str:
        outcome = self.submit(SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content(**overrides)))
        return outcome.proposal_id

    def submit_update(self, body: str = "# SSH\n\nA new body.\n", **content: Any) -> str:
        current = self.world.notes.get(SSH)
        assert current is not None
        request = SubmitProposalRequest(
            kind=ProposalKind.UPDATE,
            target=SSH,
            base_hash=content_hash(current),
            content=ProposedContent(body=body, **content),
        )
        return self.submit(request).proposal_id

    def validate(self, proposal_id: str) -> ProposalOutcome:
        return self.validate_uc().execute(ProposalIdRequest(proposal_id=proposal_id), owner_ctx())

    def curate(self, proposal_id: str) -> ProposalOutcome:
        return self.curate_uc().execute(ProposalIdRequest(proposal_id=proposal_id), owner_ctx())

    def review(self, proposal_id: str, action: ReviewAction = ReviewAction.APPROVE, **kwargs: Any) -> ProposalOutcome:
        request = ReviewProposalRequest(proposal_id=proposal_id, action=action, **kwargs)
        return self.review_uc().execute(request, owner_ctx())

    def apply(self, proposal_id: str, c: AccessContext | None = None) -> ApplyProposalResult:
        return self.apply_uc().execute(ProposalIdRequest(proposal_id=proposal_id), c or owner_ctx())

    def approved(self, proposal_id: str, *, override: Decision | None = None) -> str:
        """Run the whole pipeline up to ``approved``."""
        self.validate(proposal_id)
        self.curate(proposal_id)
        self.review(proposal_id, override=override)
        return proposal_id

    def stored(self, proposal_id: str) -> Proposal:
        proposal = self.store.get(proposal_id)
        assert proposal is not None
        return proposal

    @property
    def commits(self) -> int:
        return len(self.world.notes.commits)


def note_hash(kit: Kit, note_id: NoteId = SSH) -> str:
    note = kit.world.notes.get(note_id)
    assert note is not None
    return content_hash(note)
