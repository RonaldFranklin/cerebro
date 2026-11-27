"""Bounded owner history. The browser authentication middleware requires a restricted owner session."""

from dataclasses import asdict

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from cerebro.application.history import ChangeUnavailable, HistoryCursor, HistoryRequest
from cerebro.application.use_cases.history import OwnerHistory
from cerebro.domain.note import NoteId


def install_browser_history(app: FastAPI, history: OwnerHistory) -> None:
    # A continuation can point to an unattributed or unreadable revision. Keep its id private.
    cursors = Fernet(Fernet.generate_key())

    def encode(cursor: HistoryCursor | None) -> str | None:
        return cursors.encrypt(f"{cursor.head}:{cursor.start}".encode()).decode() if cursor else None

    def decode(cursor: str | None) -> HistoryCursor | None:
        if cursor is None:
            return None
        head, start = cursors.decrypt(cursor.encode()).decode().split(":")
        return HistoryCursor(head, start)

    def page_of(request: Request, cursor: str | None, note_id: NoteId | None) -> JSONResponse:
        try:
            page = history.list(request.state.ctx, decode(cursor), note_id=note_id)
        except InvalidToken, ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        return JSONResponse(
            {
                "changes": [asdict(row) for row in page.changes],
                "next_cursor": encode(page.next_cursor),
                "page_cursor": encode(page.page_cursor),
            }
        )

    @app.get("/api/changes")
    def list_changes(request: Request, cursor: str | None = None) -> JSONResponse:
        return page_of(request, cursor, None)

    @app.get("/api/notes/{note_id}/changes")
    def note_changes(request: Request, note_id: str, cursor: str | None = None) -> JSONResponse:
        # An invalid id raises InvalidValueError, which the API reports as an invalid request.
        return page_of(request, cursor, NoteId(note_id))

    @app.get("/api/changes/{revision}")
    def change_detail(revision: str, request: Request, cursor: str | None = None) -> JSONResponse:
        try:
            result = history.detail(HistoryRequest(revision, decode(cursor)), request.state.ctx)
        except InvalidToken, ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        return JSONResponse({**asdict(result.change), "diff": result.diff, "truncated": result.truncated})
