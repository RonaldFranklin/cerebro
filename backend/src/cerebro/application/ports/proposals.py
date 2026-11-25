"""``ProposalStore``: proposals and their decisions (the audit trail)."""

from typing import Protocol

from cerebro.domain.proposal import Proposal, ProposalStatus


class ProposalStore(Protocol):
    def add(self, proposal: Proposal) -> None:
        """Store a new proposal (``revision`` must be 0). Raises ``AlreadyExists`` if the id is taken."""
        ...

    def get(self, proposal_id: str) -> Proposal | None: ...

    def save(self, proposal: Proposal, *, expected_revision: int) -> Proposal:
        """Replace a stored proposal (after a lifecycle transition), compare-and-swap on ``revision``.

        Succeeds only if the stored revision equals ``expected_revision`` (the revision of the copy the caller
        loaded); the stored copy gets ``expected_revision + 1`` and is returned. Raises ``ProposalNotFound`` if
        unknown and ``ProposalConflict`` if the revision differs. The check and the write are atomic.
        """
        ...

    def find(self, *, status: ProposalStatus | None = None, source_agent: str | None = None) -> tuple[Proposal, ...]:
        """Proposals matching every given filter, oldest first (``created_at``, then id)."""
        ...
