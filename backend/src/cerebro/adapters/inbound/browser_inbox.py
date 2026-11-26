"""Private owner review of proposals at the revision shown by the browser."""

from dataclasses import dataclass
from difflib import unified_diff

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from cerebro.adapters.inbound.browser_write_views import validation_view, warnings_view
from cerebro.adapters.inbound.read_views import read_view
from cerebro.application.dto import (
    AmendmentResult,
    AmendProposalRequest,
    ApplyOutcome,
    ApplyProposalResult,
    InboxItem,
    InboxPreview,
    InboxResult,
    ListProposalsRequest,
    ProposalIdRequest,
    ProposalOutcome,
    ReviewAction,
    ReviewProposalRequest,
)
from cerebro.application.errors import (
    AmendmentValidationError,
    ProposalConflict,
    ProposalNotApplicable,
    ProposalNotFound,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.decision import MAX_REASON_LENGTH, Decision
from cerebro.domain.errors import ContentConflict, InvalidTransition, InvalidValueError
from cerebro.domain.proposal import ProposalKind, validate_proposal_id


@dataclass(frozen=True, slots=True)
class BrowserInbox:
    listing: ContextUseCase[ListProposalsRequest, InboxResult]
    amend: ContextUseCase[AmendProposalRequest, AmendmentResult]
    review: ContextUseCase[ReviewProposalRequest, ProposalOutcome]
    apply: ContextUseCase[ProposalIdRequest, ApplyProposalResult]


class Revision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    revision: int = Field(ge=0, le=2**63 - 1)


class Amendment(Revision):
    body: str = Field(min_length=1)


class Rejection(Revision):
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)


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


_DIFF_CHARS = 32768
_DIFF_LINES = 512
_FIELDS = (
    "id",
    "title",
    "type",
    "area",
    "priority",
    "status",
    "sensitivity",
    "summary",
    "hub",
    "related",
    "superseded_by",
    "lang",
    "created",
    "updated",
    "review_by",
)


def _preview(preview: InboxPreview) -> dict[str, object]:
    base = read_view(preview.base) if preview.base else None
    candidate = read_view(preview.candidate) if preview.candidate else None
    before, after = base or {}, candidate or {}
    fields = {
        name: {"before": before.get(name), "after": after.get(name)}
        for name in _FIELDS
        if before.get(name) != after.get(name)
    }
    left = preview.base.body if preview.base else ""
    right = preview.candidate.body if preview.candidate else ""
    truncated = any(
        len(body) > _DIFF_CHARS or len(body[:_DIFF_CHARS].splitlines()) > _DIFF_LINES for body in (left, right)
    )
    lines = unified_diff(
        left[:_DIFF_CHARS].splitlines(keepends=True)[:_DIFF_LINES],
        right[:_DIFF_CHARS].splitlines(keepends=True)[:_DIFF_LINES],
        fromfile=preview.base_label,
        tofile="candidate",
    )
    body_diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    decision = preview.decision
    return {
        "base": base,
        "candidate": candidate,
        "base_label": preview.base_label,
        "stale_base": preview.stale_base,
        "validation": validation_view(preview.validation) if preview.validation else None,
        "historical_validation": validation_view(preview.historical_validation)
        if preview.historical_validation
        else None,
        "decision": {
            "kind": decision.kind.value,
            "target": decision.target.value if decision.target else None,
            "reason": decision.reason,
        }
        if decision
        else None,
        "diff": {
            "fields": fields,
            "body": body_diff[:_DIFF_CHARS],
            "truncated": truncated or len(body_diff) > _DIFF_CHARS,
        },
    }


def _apply_response(result: ApplyProposalResult) -> JSONResponse:
    status = {ApplyOutcome.CONFLICT: 409, ApplyOutcome.INVALID: 422}.get(result.outcome, 200)
    return JSONResponse(
        {
            "id": result.proposal_id,
            "status": result.status.value,
            "outcome": result.outcome.value,
            "note_id": result.note_id.value if result.note_id else None,
            "commit": result.revision,
            "warnings": warnings_view(result.warnings),
            "validation": validation_view(result.validation) if result.validation else None,
        },
        status,
    )


def install_browser_write_errors(app: FastAPI) -> None:
    @app.exception_handler(ProposalNotFound)
    async def absent(request: Request, error: ProposalNotFound) -> JSONResponse:
        return JSONResponse({"error": "Not found."}, 404)

    @app.exception_handler(ContentConflict)
    @app.exception_handler(ProposalConflict)
    @app.exception_handler(InvalidTransition)
    @app.exception_handler(ProposalNotApplicable)
    async def conflict(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"error": "Conflict. Reload before retrying."}, 409)

    @app.exception_handler(AmendmentValidationError)
    async def invalid(request: Request, error: AmendmentValidationError) -> JSONResponse:
        return JSONResponse({"validation": validation_view(error.report)}, 422)

    @app.exception_handler(InvalidValueError)
    async def invalid_value(request: Request, error: InvalidValueError) -> JSONResponse:
        return JSONResponse({"error": "Invalid request."}, 422)


def install_browser_inbox(app: FastAPI, cases: BrowserInbox) -> None:
    @app.get("/api/inbox")
    def listing(request: Request) -> dict[str, object]:
        result = cases.listing.execute(ListProposalsRequest(), request.state.ctx)
        return {"items": [_metadata(item) for item in result.items]}

    def inspected(proposal_id: str, request: Request) -> tuple[InboxItem, InboxPreview]:
        result = cases.listing.execute(
            ListProposalsRequest(proposal_id=validate_proposal_id(proposal_id)), request.state.ctx
        )
        if result.preview is None:
            raise ProposalNotApplicable("preview unavailable")
        return result.items[0], result.preview

    @app.get("/api/inbox/{proposal_id}")
    def preview(proposal_id: str, request: Request) -> dict[str, object]:
        item, detail = inspected(proposal_id, request)
        return {**_metadata(item), **_preview(detail)}

    @app.post("/api/inbox/{proposal_id}/amend")
    def amend(proposal_id: str, body: Amendment, request: Request) -> dict[str, object]:
        result = cases.amend.execute(
            AmendProposalRequest(
                proposal_id=validate_proposal_id(proposal_id), expected_revision=body.revision, body=body.body
            ),
            request.state.ctx,
        )
        return {"id": result.proposal_id, "revision": result.revision}

    @app.post("/api/inbox/{proposal_id}/approve")
    def approve(proposal_id: str, body: Revision, request: Request) -> JSONResponse:
        item, detail = inspected(proposal_id, request)
        if item.revision != body.revision or detail.stale_base:
            raise ProposalConflict(proposal_id)
        override = None
        if item.kind is ProposalKind.CREATE and detail.decision and detail.decision.target is None and detail.candidate:
            override = Decision.create(detail.candidate.id)
        approved = cases.review.execute(
            ReviewProposalRequest(
                proposal_id=proposal_id,
                expected_revision=body.revision,
                action=ReviewAction.APPROVE,
                require_fresh_base=True,
                override=override,
            ),
            request.state.ctx,
        )
        if approved.revision is None:
            raise ProposalConflict(proposal_id)
        return _apply_response(
            cases.apply.execute(
                ProposalIdRequest(proposal_id=proposal_id, expected_revision=approved.revision), request.state.ctx
            )
        )

    @app.post("/api/inbox/{proposal_id}/resume")
    def resume(proposal_id: str, body: Revision, request: Request) -> JSONResponse:
        return _apply_response(
            cases.apply.execute(
                ProposalIdRequest(proposal_id=validate_proposal_id(proposal_id), expected_revision=body.revision),
                request.state.ctx,
            )
        )

    @app.post("/api/inbox/{proposal_id}/reject")
    def reject(proposal_id: str, body: Rejection, request: Request) -> dict[str, object]:
        result = cases.review.execute(
            ReviewProposalRequest(
                proposal_id=validate_proposal_id(proposal_id),
                expected_revision=body.revision,
                action=ReviewAction.REJECT,
                reason=body.reason,
            ),
            request.state.ctx,
        )
        return {"id": result.proposal_id, "status": result.status.value}
