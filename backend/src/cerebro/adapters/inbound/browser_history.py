"""Bounded private owner history. The browser authentication middleware enforces the private listener."""

from dataclasses import asdict

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from cerebro.application.history import ChangeUnavailable, HistoryRequest
from cerebro.application.use_cases.history import OwnerHistory


def install_browser_history(app: FastAPI, history: OwnerHistory) -> None:
    # A continuation can point to an unattributed or unreadable revision. Keep its id private.
    cursors = Fernet(Fernet.generate_key())

    @app.get("/api/changes")
    def list_changes(request: Request, cursor: str | None = None) -> JSONResponse:
        try:
            start = cursors.decrypt(cursor.encode("utf-8")).decode("ascii") if cursor is not None else None
            page = history.list(request.state.ctx, start)
        except InvalidToken, ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        next_cursor = cursors.encrypt(page.next_cursor.encode()).decode() if page.next_cursor else None
        return JSONResponse({"changes": [asdict(row) for row in page.changes], "next_cursor": next_cursor})

    @app.get("/api/changes/{revision}")
    def change_detail(revision: str, request: Request) -> JSONResponse:
        try:
            result = history.detail(HistoryRequest(revision), request.state.ctx)
        except ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        return JSONResponse({**asdict(result.change), "diff": result.diff, "truncated": result.truncated})
