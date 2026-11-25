"""``ProposalStore``: proposals and their decisions (the audit trail)."""

from typing import Protocol

from cerebro.domain.proposal import Proposal, ProposalStatus


class ProposalStore(Protocol):
    def add(self, proposal: Proposal) -> None:
        """Store a new proposal. Raises ``AlreadyExists`` if the id is taken."""
        ...

    def get(self, proposal_id: str) -> Proposal | None: ...

    def save(self, proposal: Proposal) -> None:
        """Replace a stored proposal (after a lifecycle transition). Raises ``ProposalNotFound`` if unknown."""
        ...

    def find(self, *, status: ProposalStatus | None = None, source_agent: str | None = None) -> tuple[Proposal, ...]:
        """Proposals matching every given filter, oldest first (``created_at``, then id)."""
        ...
