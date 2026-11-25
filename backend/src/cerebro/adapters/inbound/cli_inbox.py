"""Owner-directed output for the Inbox commands."""

import json

from cerebro.adapters.inbound.cli_views import note_view
from cerebro.application.dto import (
    ApplyProposalResult,
    InboxItem,
    InboxResult,
    ListProposalsRequest,
    ProposalIdRequest,
    ProposalOutcome,
    ReviewAction,
    ReviewProposalRequest,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessContext
from cerebro.domain.proposal import ProposalStatus


def _metadata(item: InboxItem) -> dict[str, object]:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "status": item.status.value,
        "revision": item.revision,
        "source_agent": item.source_agent,
        "target": item.target.value if item.target else None,
        "conflict": item.conflict,
    }


def _listing(result: InboxResult) -> object:
    if result.preview is None:
        return [_metadata(item) for item in result.items]
    (item,) = result.items
    decision = result.preview.decision
    return {
        **_metadata(item),
        "decision": {
            "kind": decision.kind.value,
            "reason": decision.reason,
            "target": decision.target.value if decision.target else None,
        }
        if decision
        else None,
        "candidate": note_view(result.preview.candidate) if result.preview.candidate else None,
    }


def run_inbox_command(
    request: ListProposalsRequest | ReviewProposalRequest | ProposalIdRequest,
    ctx: AccessContext,
    *,
    listing: ContextUseCase[ListProposalsRequest, InboxResult],
    review: ContextUseCase[ReviewProposalRequest, ProposalOutcome],
    apply: ContextUseCase[ProposalIdRequest, ApplyProposalResult],
) -> int:
    if isinstance(request, ListProposalsRequest):
        print(json.dumps(_listing(listing.execute(request, ctx)), indent=2))
        return 0
    if isinstance(request, ReviewProposalRequest):
        result = review.execute(request, ctx)
        if request.action is ReviewAction.REJECT:
            print(json.dumps({"id": result.proposal_id, "status": result.status.value}))
            return 0
    applied = apply.execute(ProposalIdRequest(proposal_id=request.proposal_id), ctx)
    print(
        json.dumps(
            {
                "id": applied.proposal_id,
                "status": applied.status.value,
                "outcome": applied.outcome.value,
                "note_id": applied.note_id.value if applied.note_id else None,
                "commit": applied.revision,
                "warnings": [{"code": w.code.value, "message": w.message} for w in applied.warnings],
            },
            indent=2,
        )
    )
    return 0 if applied.status is ProposalStatus.APPLIED else 1
