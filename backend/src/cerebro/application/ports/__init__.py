"""Ports: ``typing.Protocol`` interfaces that adapters implement. Depend only on ``cerebro.domain``."""

from cerebro.application.ports.curator import CurationRequest, Curator
from cerebro.application.ports.graph import GraphIndex, GraphSlice
from cerebro.application.ports.notes import NoteChange, NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.search import MAX_LIMIT, SearchHit, SearchIndex, SearchQuery
from cerebro.application.ports.system import Cipher, Clock, IdGenerator, VersionControl
from cerebro.application.ports.tokens import TokenStore

__all__ = [
    "MAX_LIMIT",
    "Cipher",
    "Clock",
    "CurationRequest",
    "Curator",
    "GraphIndex",
    "GraphSlice",
    "IdGenerator",
    "NoteChange",
    "NoteRepository",
    "ProposalStore",
    "SearchHit",
    "SearchIndex",
    "SearchQuery",
    "TokenStore",
    "VersionControl",
]
