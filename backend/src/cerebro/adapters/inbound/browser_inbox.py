"""Private owner access to historical proposal previews."""

from difflib import unified_diff

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from cerebro.adapters.inbound.browser_write_views import validation_view
from cerebro.adapters.inbound.read_views import read_view
from cerebro.application.dto import (
    InboxItem,
    InboxPreview,
    InboxResult,
    ListProposalsRequest,
)
from cerebro.application.errors import (
    ProposalNotApplicable,
    ProposalNotFound,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.proposal import validate_proposal_id


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


def install_browser_write_errors(app: FastAPI) -> None:
    @app.exception_handler(ProposalNotFound)
    async def absent(request: Request, error: ProposalNotFound) -> JSONResponse:
        return JSONResponse({"error": "Not found."}, 404)

    @app.exception_handler(ContentConflict)
    @app.exception_handler(ProposalNotApplicable)
    async def conflict(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"error": "Conflict. Reload before retrying."}, 409)

    @app.exception_handler(InvalidValueError)
    async def invalid_value(request: Request, error: InvalidValueError) -> JSONResponse:
        return JSONResponse({"error": "Invalid request."}, 422)


def install_browser_inbox(app: FastAPI, history: ContextUseCase[ListProposalsRequest, InboxResult]) -> None:
    @app.get("/api/inbox")
    def listing(request: Request) -> dict[str, object]:
        result = history.execute(ListProposalsRequest(), request.state.ctx)
        return {"items": [_metadata(item) for item in result.items]}

    def inspected(proposal_id: str, request: Request) -> tuple[InboxItem, InboxPreview]:
        result = history.execute(ListProposalsRequest(proposal_id=validate_proposal_id(proposal_id)), request.state.ctx)
        if result.preview is None:
            raise ProposalNotApplicable("preview unavailable")
        return result.items[0], result.preview

    @app.get("/api/inbox/{proposal_id}")
    def preview(proposal_id: str, request: Request) -> dict[str, object]:
        item, detail = inspected(proposal_id, request)
        return {**_metadata(item), **_preview(detail)}
