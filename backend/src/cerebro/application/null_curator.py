"""``NullCurator``: the default ``Curator``, used until an LLM curator is configured (ADR-0007)."""

from cerebro.application.ports.curator import CurationRequest
from cerebro.application.proposal_support import provisional_note_id
from cerebro.domain.decision import Decision

NULL_CURATOR_NAME = "null-curator"
REASON = "no curator is configured: the owner decides"


class NullCurator:
    """Sends every proposal to the owner, suggesting the note id (derived from the title for a ``create``)."""

    @property
    def name(self) -> str:
        return NULL_CURATOR_NAME

    def curate(self, request: CurationRequest) -> Decision:
        proposal = request.proposal
        target = proposal.target or provisional_note_id(proposal.content.title)
        return Decision.needs_owner(REASON, target=target)
