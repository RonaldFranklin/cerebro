"""Use cases: one class per use case, ports injected in ``__init__``, a single ``execute(request, ctx)``."""

from cerebro.application.use_cases.apply_proposal import ApplyProposal
from cerebro.application.use_cases.authenticate_token import AuthenticateToken
from cerebro.application.use_cases.curate_proposal import CurateProposal
from cerebro.application.use_cases.get_graph import GetGraph
from cerebro.application.use_cases.get_neighbors import GetNeighbors
from cerebro.application.use_cases.get_overview import GetOverview
from cerebro.application.use_cases.health_report import HealthReport
from cerebro.application.use_cases.issue_agent_token import IssueAgentToken
from cerebro.application.use_cases.owner_edit_note import OwnerEditNote
from cerebro.application.use_cases.read_note import ReadNote
from cerebro.application.use_cases.rebuild_index import RebuildIndex
from cerebro.application.use_cases.review_proposal import ReviewProposal
from cerebro.application.use_cases.revoke_agent_token import RevokeAgentToken
from cerebro.application.use_cases.search_notes import SearchNotes
from cerebro.application.use_cases.submit_proposal import SubmitProposal
from cerebro.application.use_cases.validate_proposal import ValidateProposal

__all__ = [
    "ApplyProposal",
    "AuthenticateToken",
    "CurateProposal",
    "GetGraph",
    "GetNeighbors",
    "GetOverview",
    "HealthReport",
    "IssueAgentToken",
    "OwnerEditNote",
    "ReadNote",
    "RebuildIndex",
    "ReviewProposal",
    "RevokeAgentToken",
    "SearchNotes",
    "SubmitProposal",
    "ValidateProposal",
]
