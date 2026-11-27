"""Owner browser edits of a complete, previously inspected note."""

from datetime import date
from typing import Annotated

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from cerebro.adapters.inbound.browser_write_views import validation_view, warnings_view
from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_INPUT_BYTES
from cerebro.application.dto import CheckOwnerEditResult, OwnerEditNoteRequest, OwnerEditNoteResult
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.errors import ContentConflict
from cerebro.domain.frontmatter import parse_note
from cerebro.domain.note import MAX_NOTE_ID_LENGTH, Note
from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


type _Id = Annotated[str, Field(max_length=MAX_NOTE_ID_LENGTH)]
_BYTE_LIMITS = {
    "title": 480,
    "type": MAX_FIELD_BYTES,
    "area": MAX_FIELD_BYTES,
    "priority": MAX_FIELD_BYTES,
    "sensitivity": MAX_FIELD_BYTES,
    "status": MAX_FIELD_BYTES,
    "summary": 1200,
    "lang": MAX_FIELD_BYTES,
}


class EditableNote(_Closed):
    title: str
    type: str
    area: str
    priority: str
    sensitivity: str
    status: str
    summary: str
    hub: _Id | None
    related: list[_Id] = Field(max_length=256)
    superseded_by: _Id | None
    lang: str
    review_by: str | None = Field(max_length=10)
    body: str = Field(max_length=MAX_INPUT_BYTES)


class Edit(_Closed):
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
    note: EditableNote


def _parse(note_id: str, payload: Edit) -> Note | ValidationReport:
    raw = payload.note.model_dump(exclude={"body"})
    oversized = tuple(
        Violation(
            code=ViolationCode.INVALID_FIELD,
            field=name,
            message="Field exceeds its UTF-8 byte limit.",
            severity=Severity.ERROR,
        )
        for name, maximum in _BYTE_LIMITS.items()
        if len(raw[name].encode("utf-8")) > maximum
    )
    if oversized:
        return ValidationReport(oversized)
    # The use cases keep the stored creation date and assign their clock's date on update.
    raw.update(id=note_id, created=date.min, updated=date.min)
    return parse_note(raw, payload.note.body)


def install_browser_edit(
    app: FastAPI,
    owner_edit_note: ContextUseCase[OwnerEditNoteRequest, OwnerEditNoteResult],
    check_owner_edit: ContextUseCase[OwnerEditNoteRequest, CheckOwnerEditResult],
) -> None:
    @app.exception_handler(ContentConflict)
    async def conflict(request: Request, error: ContentConflict) -> JSONResponse:
        return JSONResponse({"error": "Conflict. Reload before retrying."}, 409)

    @app.post("/api/notes/{note_id}/validate")
    def check(request: Request, note_id: str, payload: Edit) -> JSONResponse:
        note = _parse(note_id, payload)
        if isinstance(note, ValidationReport):
            # No note could be built, so nothing else was checked.
            return JSONResponse(
                {
                    "validation": validation_view(note),
                    "budget": None,
                    "broken_link_lines": [],
                    "sensitivity_lines": [],
                    "base_current": None,
                }
            )
        outcome = check_owner_edit.execute(
            OwnerEditNoteRequest(note=note, expected_hash=payload.expected_hash), request.state.ctx
        )
        return JSONResponse(
            {
                "validation": validation_view(outcome.validation),
                "budget": {"words": outcome.words, "soft": outcome.soft_limit, "hard": outcome.hard_limit},
                "broken_link_lines": list(outcome.broken_link_lines),
                "sensitivity_lines": list(outcome.sensitivity_lines),
                "base_current": outcome.base_current,
            }
        )

    @app.put("/api/notes/{note_id}")
    def edit(request: Request, note_id: str, payload: Edit) -> JSONResponse:
        note = _parse(note_id, payload)
        if isinstance(note, ValidationReport):
            return JSONResponse({"validation": validation_view(note)}, 422)
        outcome = owner_edit_note.execute(
            OwnerEditNoteRequest(note=note, expected_hash=payload.expected_hash), request.state.ctx
        )
        report = validation_view(outcome.validation)
        if not outcome.validation.ok:
            return JSONResponse({"validation": report}, 422)
        return JSONResponse(
            {
                "id": outcome.note_id.value,
                "commit": outcome.revision,
                "content_hash": outcome.content_hash,
                "validation": report,
                "warnings": warnings_view(outcome.warnings),
            }
        )
