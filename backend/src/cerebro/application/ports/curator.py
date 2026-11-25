"""``Curator``: suggests a decision for a validated proposal. Adapters: LLM providers and ``NullCurator``."""

from dataclasses import dataclass
from typing import Protocol

from cerebro.application.ports.search import SearchHit
from cerebro.domain.decision import Decision
from cerebro.domain.note import Note
from cerebro.domain.proposal import Proposal


@dataclass(frozen=True, slots=True, kw_only=True)
class CurationRequest:
    """Everything the curator may look at: the proposal, the note it targets (if any) and similar notes found by
    the index. The use case caps the target and the similar notes at ``internal`` (ADR-0016): the target's body
    links and its ``hub`` / ``related`` / ``superseded_by`` to hidden notes are removed, and a proposal touching
    a restricted note never reaches the curator.
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
