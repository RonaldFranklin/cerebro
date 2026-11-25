"""In-memory ``ProposalStore`` and ``TokenStore``, and a scriptable ``Curator``."""

from dataclasses import replace

from cerebro.application.errors import AlreadyExists, ProposalConflict, ProposalNotFound, TokenNotFound
from cerebro.application.ports.curator import CurationRequest
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.decision import Decision
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.proposal import Proposal, ProposalStatus


class InMemoryProposalStore:
    def __init__(self) -> None:
        self._items: dict[str, Proposal] = {}

    def add(self, proposal: Proposal) -> None:
        if proposal.revision != 0:
            raise InvalidValueError("revision", "a new proposal starts at revision 0")
        if proposal.id in self._items:
            raise AlreadyExists("proposal", proposal.id)
        self._items[proposal.id] = proposal

    def get(self, proposal_id: str) -> Proposal | None:
        return self._items.get(proposal_id)

    def save(self, proposal: Proposal, *, expected_revision: int) -> Proposal:
        stored = self._items.get(proposal.id)
        if stored is None:
            raise ProposalNotFound(proposal.id)
        if stored.revision != expected_revision:
            raise ProposalConflict(proposal.id)
        saved = replace(proposal, revision=expected_revision + 1)
        self._items[proposal.id] = saved
        return saved

    def find(self, *, status: ProposalStatus | None = None, source_agent: str | None = None) -> tuple[Proposal, ...]:
        found = [
            p
            for p in self._items.values()
            if (status is None or p.status is status) and (source_agent is None or p.source_agent == source_agent)
        ]
        return tuple(sorted(found, key=lambda p: (p.created_at, p.id)))


class InMemoryTokenStore:
    def __init__(self) -> None:
        self._items: dict[str, AgentToken] = {}

    def add(self, token: AgentToken) -> None:
        if token.id in self._items:
            raise AlreadyExists("token", token.id)
        if self.find_by_hash(token.token_hash) is not None:
            raise AlreadyExists("token hash", token.id)
        self._items[token.id] = token

    def get(self, token_id: str) -> AgentToken | None:
        return self._items.get(token_id)

    def find_by_hash(self, token_hash: str) -> AgentToken | None:
        return next((t for t in self._items.values() if t.token_hash == token_hash), None)

    def save(self, token: AgentToken) -> None:
        if token.id not in self._items:
            raise TokenNotFound(token.id)
        self._items[token.id] = token

    def all(self) -> tuple[AgentToken, ...]:
        return tuple(sorted(self._items.values(), key=lambda t: (t.created_at, t.id)))


class FakeCurator:
    """Returns the scripted decision; by default it sends everything to the owner (what ``NullCurator`` will do)."""

    def __init__(self, decision: Decision | None = None, name: str = "fake-curator") -> None:
        self._decision = decision or Decision.needs_owner("no curator configured")
        self._name = name
        self.requests: list[CurationRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def curate(self, request: CurationRequest) -> Decision:
        self.requests.append(request)
        return self._decision
