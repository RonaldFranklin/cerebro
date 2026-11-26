"""Private browser edits of a complete, previously inspected note."""

from datetime import date
from typing import Annotated

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from cerebro.adapters.inbound.browser_write_views import validation_view, warnings_view
from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_INPUT_BYTES
from cerebro.application.dto import OwnerEditNoteRequest, OwnerEditNoteResult
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.frontmatter import parse_note
from cerebro.domain.note import MAX_NOTE_ID_LENGTH
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


def install_browser_edit(
    app: FastAPI, owner_edit_note: ContextUseCase[OwnerEditNoteRequest, OwnerEditNoteResult]
) -> None:
    @app.put("/api/notes/{note_id}")
    def edit(request: Request, note_id: str, payload: Edit) -> JSONResponse:
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
            return JSONResponse({"validation": validation_view(ValidationReport(oversized))}, 422)
        # OwnerEditNote keeps the stored creation date and assigns its clock's date on update.
        raw.update(id=note_id, created=date.min, updated=date.min)
        note = parse_note(raw, payload.note.body)
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
