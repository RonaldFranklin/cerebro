"""``Curator``: suggests a decision for a validated proposal. Adapters: LLM providers and ``NullCurator``."""

from dataclasses import dataclass
from typing import Protocol

from cerebro.application.ports.search import SearchHit
from cerebro.domain.decision import Decision
from cerebro.domain.note import Note
from cerebro.domain.proposal import Proposal


@dataclass(frozen=True, slots=True, kw_only=True)
class CurationRequest:
    """Everything the curator may look at: the proposal, the note it targets (if any, already checked against
    the owner's ceiling by the use case) and similar notes found by the index.
    """

    proposal: Proposal
    target: Note | None = None
    similar: tuple[SearchHit, ...] = ()


class Curator(Protocol):
    @property
    def name(self) -> str:
        """Identity recorded as ``Curated-by`` (letters, digits and ``. _ / @ + -``) and **must start
        with ``curator/``** (e.g. ``curator/null``, ``curator/anthropic-triage``): agent names cannot contain ``/``, so
        an agent can never appear as a curator in the audit trail."""
        ...

    def curate(self, request: CurationRequest) -> Decision:
        """Suggest a decision. It only advises: the owner approves."""
        ...
