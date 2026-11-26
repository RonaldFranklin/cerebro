"""Bounded private owner history. The browser authentication middleware enforces the private listener."""

from dataclasses import asdict

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from cerebro.application.history import ChangeUnavailable, HistoryCursor, HistoryRequest
from cerebro.application.use_cases.history import OwnerHistory


def install_browser_history(app: FastAPI, history: OwnerHistory) -> None:
    # A continuation can point to an unattributed or unreadable revision. Keep its id private.
    cursors = Fernet(Fernet.generate_key())

    def decode(cursor: str | None) -> HistoryCursor | None:
        if cursor is None:
            return None
        head, start = cursors.decrypt(cursor.encode()).decode().split(":")
        return HistoryCursor(head, start)

    @app.get("/api/changes")
    def list_changes(request: Request, cursor: str | None = None) -> JSONResponse:
        try:
            page = history.list(request.state.ctx, decode(cursor))
        except InvalidToken, ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        following = page.next_cursor
        next_cursor = cursors.encrypt(f"{following.head}:{following.start}".encode()).decode() if following else None
        return JSONResponse({"changes": [asdict(row) for row in page.changes], "next_cursor": next_cursor})

    @app.get("/api/changes/{revision}")
    def change_detail(revision: str, request: Request, cursor: str | None = None) -> JSONResponse:
        try:
            result = history.detail(HistoryRequest(revision, decode(cursor)), request.state.ctx)
        except InvalidToken, ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        return JSONResponse({**asdict(result.change), "diff": result.diff, "truncated": result.truncated})
