"""Use cases: one class per use case, ports injected in ``__init__``, a single ``execute(request, ctx)``."""

from cerebro.application.use_cases.apply_proposal import ApplyProposal
from cerebro.application.use_cases.curate_proposal import CurateProposal
from cerebro.application.use_cases.get_graph import GetGraph
from cerebro.application.use_cases.get_neighbors import GetNeighbors
from cerebro.application.use_cases.get_overview import GetOverview
from cerebro.application.use_cases.owner_edit_note import OwnerEditNote
from cerebro.application.use_cases.read_note import ReadNote
from cerebro.application.use_cases.review_proposal import ReviewProposal
from cerebro.application.use_cases.search_notes import SearchNotes
from cerebro.application.use_cases.submit_proposal import SubmitProposal
from cerebro.application.use_cases.validate_proposal import ValidateProposal

__all__ = [
    "ApplyProposal",
    "CurateProposal",
    "GetGraph",
    "GetNeighbors",
    "GetOverview",
    "OwnerEditNote",
    "ReadNote",
    "ReviewProposal",
    "SearchNotes",
    "SubmitProposal",
    "ValidateProposal",
]
